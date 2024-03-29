# Copyright (C) 2013 Google Inc. All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are
# met:
#
#     * Redistributions of source code must retain the above copyright
# notice, this list of conditions and the following disclaimer.
#     * Redistributions in binary form must reproduce the above
# copyright notice, this list of conditions and the following disclaimer
# in the documentation and/or other materials provided with the
# distribution.
#     * Neither the name of Google Inc. nor the names of its
# contributors may be used to endorse or promote products derived from
# this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
# A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
# OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
# SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
# LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
# DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
# THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

"""Blink IDL Intermediate Representation (IR) classes.

Classes are primarily constructors, which build an IdlDefinitions object
(and various contained objects) from an AST (produced by blink_idl_parser).

IR stores typedefs and they are resolved by the code generator.

Typedef resolution uses some auxiliary classes and OOP techniques to make this
a generic call. See TypedefResolver class in code_generator_v8.py.

Class hierarchy (mostly containment, '<' for inheritance):

IdlDefinitions
    IdlCallbackFunction < TypedObject
    IdlEnum :: FIXME: remove, just use a dict for enums
    IdlInterface
        IdlAttribute < TypedObject
        IdlConstant < TypedObject
        IdlLiteral
        IdlOperation < TypedObject
            IdlArgument < TypedObject
        IdlStringifier
        IdlIterable < IdlIterableOrMaplikeOrSetlike
        IdlMaplike < IdlIterableOrMaplikeOrSetlike
        IdlSetlike < IdlIterableOrMaplikeOrSetlike

TypedObject :: Object with one or more attributes that is a type.

IdlArgument is 'picklable', as it is stored in interfaces_info.

Design doc: http://www.chromium.org/developers/design-documents/idl-compiler
"""

from __future__ import annotations

import re
import os
import abc
import random

from typing import Dict, List, Tuple, Union

from IDLParserTool.idl_types import IdlAnnotatedType
from IDLParserTool.idl_types import IdlFrozenArrayType
from IDLParserTool.idl_types import IdlNullableType
from IDLParserTool.idl_types import IdlRecordType
from IDLParserTool.idl_types import IdlSequenceType
from IDLParserTool.idl_types import IdlType
from IDLParserTool.idl_types import IdlUnionType
from IDLParserTool.idl_types import IdlPromiseType

from utils import NumberRangeEnd, NumberRange

SPECIAL_KEYWORD_LIST = ['GETTER', 'SETTER', 'DELETER']

def ProcessNumberEnum(idl_type:IdlType, raw_value):
    match = re.match(r"\([\d,\s]+\)", raw_value)
    assert match
    str_values = re.findall(r"(\d+),?\s?", raw_value)
    caster = int if idl_type.is_integer_type else float
    return [caster(i) for i in str_values]

################################################################################
# TypedObject
################################################################################


class TypedObject(object):
    """Object with a type, such as an Attribute or Operation (return value).

    The type can be an actual type, or can be a typedef, which must be resolved
    by the TypedefResolver before passing data to the code generator.
    """
    __metaclass__ = abc.ABCMeta
    idl_type_attributes = ('idl_type', )


################################################################################
# Definitions (main container class)
################################################################################

class IdlDefinitions(object):
    def __init__(self, node):
        """Args: node: AST root node, class == 'File'"""
        self.callback_functions = {}
        self.dictionaries = {}
        self.enumerations = {}
        self.includes = []
        self.interfaces = {}
        self.first_name = None
        self.typedefs:Dict(IdlTypedef) = {}
        self.node = node
        
        node_class = node.GetClass()
        if node_class != 'File':
            raise ValueError('Unrecognized node class: %s' % node_class)

        children = node.GetChildren()
        for child in children:
            child_class = child.GetClass()
            if child_class == 'Interface':
                interface = IdlInterface(child)
                self.interfaces[interface.name] = interface
                if not self.first_name:
                    self.first_name = interface.name
            elif child_class == 'Typedef':
                typedef = IdlTypedef(child)
                self.typedefs[typedef.name] = typedef
            elif child_class == 'Enum':
                enumeration = IdlEnum(child)
                self.enumerations[enumeration.name] = enumeration
            elif child_class == 'Callback':
                callback_function = IdlCallbackFunction(child)
                self.callback_functions[callback_function.
                                        name] = callback_function
            elif child_class == 'Includes':
                self.includes.append(IdlIncludes(child))
            elif child_class == 'Dictionary':
                dictionary = IdlDictionary(child)
                self.dictionaries[dictionary.name] = dictionary
                if not self.first_name:
                    self.first_name = dictionary.name
            else:
                raise ValueError('Unrecognized node class: %s' % child_class)
    
    @property
    def filepath(self):
        return self.node.GetProperties()['FILENAME']

    def format_includes(self):
        include_data_list = []

        for include in self.includes:
            include_data = {
                'Name': include.interface,
                'Mixin': [include.mixin]
            }
            include_data_list.append(include_data)

        return include_data_list

    def format_callbacks(self):
        callback_data_list = []

        for _, callback in self.callback_functions.items():
            callback_data = {
                'Name': callback.name,
                'Return': None,
                'Arguments': []
            }
            return_data = {
                'Type': callback.idl_type.name,
                'RawType': str(callback.idl_type)
            }
            callback_data['Return'] = return_data
            arg_idx = 1
            for arg in callback.arguments:
                arg_data = {
                    'Type': arg.idl_type.name,
                    'RawType': str(arg.idl_type),
                    'Default': arg.default_value.value if arg.default_value else None,
                    'Optional': arg.is_optional,
                    'Pos': arg_idx
                }
                arg_idx += 1
                callback_data['Arguments'].append(arg_data)
            callback_data_list.append(callback_data)

        return callback_data_list

    def format_typedefs(self):
        typedef_data_list = []

        for _, typedef in self.typedefs.items():
            typedef_data = {
                'Name': typedef.name,
                'Type': typedef.idl_type.name,
                'RawType': str(typedef.idl_type)
            }
            typedef_data_list.append(typedef_data)

        return typedef_data_list

    def format_enumerations(self):
        enumeration_data_list = []

        for _, enumeration in self.enumerations.items():
            enumeration_data = {
                'Name': enumeration.name,
                'Values': []
            }
            for value in enumeration.values:
                enumeration_data['Values'].append(value)
            enumeration_data_list.append(enumeration_data)

        return enumeration_data_list

    def format_dictionaries(self):
        dictionary_data_list = []
        for name, dictionary in self.dictionaries.items():
            dictionary_data = {
                'Name': name,
                'Members': []
            }
            for member in dictionary.members:
                member_data = {
                    'Name': member.name,
                    'Type': member.idl_type.name,
                    'RawType': str(member.idl_type),
                    'Required': member.is_required,
                    'Default': member.default_value.value if member.default_value else None
                }
                dictionary_data['Members'].append(member_data)
            dictionary_data_list.append(dictionary_data)

        return dictionary_data_list

    def format_interface(self):
        interface_data_list = []
        for name, interface in self.interfaces.items():
            exposures = interface.extended_attributes.get('Exposed')
            if not exposures:
                exposures = []
            interface_data = {
                'Name': name,
                'Exposed': [ {'Name': exposure.exposed, 'RuntimeEnabled': exposure.runtime_enabled} for exposure in exposures ],
                'Parent': '',
                'Includes': [],
                'Constructors': [],
                'Attributes': [],
                'Methods': [],
                'IsMixin': interface.is_mixin,
                'ImplementedAs': interface.extended_attributes.get('ImplementedAs'),
                'NoInterfaceObject': True if 'NoInterfaceObject' in interface.extended_attributes.keys() else False,
                'LegacyAlias': interface.extended_attributes['LegacyWindowAlias'] if interface.extended_attributes.get('LegacyWindowAlias') else ''
            }
            if interface.parent:
                interface_data['Parent'] = interface.parent

            for constructor in interface.constructors:
                if constructor.name == 'NamedConstructor':
                    constructor_name = interface.extended_attributes['NamedConstructor']
                elif interface_data['NoInterfaceObject'] and interface_data['LegacyAlias']:
                    constructor_name = interface_data['LegacyAlias']
                else:
                    constructor_name = interface.name
                constructor_data = {
                    'Name':constructor_name,
                    'Arguments': []
                }
                arg_idx = 1
                for arg in constructor.arguments:
                    arg_data = {
                        'Type': arg.idl_type.name,
                        'RawType': str(arg.idl_type),
                        'Default': arg.default_value.value if arg.default_value else None,
                        'Optional': arg.is_optional,
                        'Pos': arg_idx
                    }
                    arg_idx += 1
                    constructor_data['Arguments'].append(arg_data)
                interface_data['Constructors'].append(constructor_data)

            for attr in interface.attributes:
                attr_data = {
                    'Name': attr.name,
                    'Type': attr.idl_type.name,
                    'RawType': str(attr.idl_type),
                    'Readonly': attr.is_read_only,
                    'Static': attr.is_static
                }
                interface_data['Attributes'].append(attr_data)
            
            for method in interface.operations:
                method_data = {
                    'Name': method.name,
                    'Getter': method.is_getter,
                    'Setter': method.is_setter,
                    'Return': None,
                    'Arguments': []
                }
                return_data = {
                    'Type': method.idl_type.name,
                    'RawType': str(method.idl_type)
                }

                method_data['Return'] = return_data
                arg_idx = 1
                for arg in method.arguments:
                    arg_data = {
                        'Type': arg.idl_type.name,
                        'RawType': str(arg.idl_type),
                        'Default': arg.default_value.value if arg.default_value else None,
                        'Optional': arg.is_optional,
                        'Pos': arg_idx
                    }
                    arg_idx += 1
                    method_data['Arguments'].append(arg_data)
                interface_data['Methods'].append(method_data)
            
            interface_data_list.append(interface_data)

        return interface_data_list

    def accept(self, visitor):
        visitor.visit_definitions(self)
        for interface in self.interfaces.values():
            interface.accept(visitor)
        for callback_function in self.callback_functions.values():
            callback_function.accept(visitor)
        for dictionary in self.dictionaries.values():
            dictionary.accept(visitor)
        for enumeration in self.enumerations.values():
            enumeration.accept(visitor)
        for include in self.includes:
            include.accept(visitor)
        for typedef in self.typedefs.values():
            typedef.accept(visitor)

    def update(self, other):
        """Update with additional IdlDefinitions."""
        for interface_name, new_interface in other.interfaces.items():
            if not new_interface.is_partial:
                # Add as new interface
                self.interfaces[interface_name] = new_interface
                continue

            # Merge partial to existing interface
            try:
                self.interfaces[interface_name].merge(new_interface)
            except KeyError:
                raise Exception('Tried to merge partial interface for {0}, '
                                'but no existing interface by that name'.
                                format(interface_name))

            # Merge callbacks and enumerations
            self.enumerations.update(other.enumerations)
            self.callback_functions.update(other.callback_functions)


def arguments_node_to_arguments(node):
    # [Constructor] and [CustomConstructor] without arguments (the bare form)
    # have None instead of an arguments node, but have the same meaning as using
    # an empty argument list, [Constructor()], so special-case this.
    # http://www.w3.org/TR/WebIDL/#Constructor
    if node is None:
        return []
    return [IdlArgument(argument_node) for argument_node in node.GetChildren()]

################################################################################
# Callback Functions
################################################################################

class IdlCallbackFunction(TypedObject):
    def __init__(self, node):
        children = node.GetChildren()
        num_children = len(children)
        if num_children < 2 or num_children > 3:
            raise ValueError('Expected 2 or 3 children, got %s' % num_children)
        type_node = children[0]
        arguments_node = children[1]
        if num_children == 3:
            ext_attributes_node = children[2]
            self.extended_attributes = (
                ext_attributes_node_to_extended_attributes(ext_attributes_node)
            )
        else:
            self.extended_attributes = {}
        arguments_node_class = arguments_node.GetClass()
        if arguments_node_class != 'Arguments':
            raise ValueError(
                'Expected Arguments node, got %s' % arguments_node_class)

        self.name = node.GetName()
        self.idl_type = type_node_to_type(type_node)
        self.arguments = arguments_node_to_arguments(arguments_node)

    def accept(self, visitor):
        visitor.visit_callback_function(self)
        for argument in self.arguments:
            argument.accept(visitor)

    def __eq__(self, other):
        if self.name == other.name and self.idl_type.name == other.idl_type.name and len(self.arguments) == len(other.arguments):
            args_num = len(self.arguments)
            for i in range(0, args_num):
                if self.arguments[i] != other.arguments[i]:
                    return False
            return True
        else:
            return False

################################################################################
# Dictionary
################################################################################


class IdlDictionary(object):
    def __init__(self, node):
        self.extended_attributes = {}
        self.is_partial = bool(node.GetProperty('PARTIAL'))
        self.name = node.GetName()
        self.idl_type = IdlType(self.name)
        self.members:list[IdlDictionaryMember] = []
        self.parent = None
        for child in node.GetChildren():
            child_class = child.GetClass()
            if child_class == 'Inherit':
                self.parent = child.GetName()
            elif child_class == 'Key':
                self.members.append(IdlDictionaryMember(child))
            elif child_class == 'ExtAttributes':
                self.extended_attributes = (
                    ext_attributes_node_to_extended_attributes(child))
            else:
                raise ValueError('Unrecognized node class: %s' % child_class)
        

    def accept(self, visitor):
        visitor.visit_dictionary(self)
        for member in self.members:
            member.accept(visitor)

    def __eq__(self, other):
        if self.name == other.name and len(self.members) == len(other.members):
            args_num = len(self.members)
            for i in range(0, args_num):
                if self.members[i] != other.members[i]:
                    return False
            return True
        else:
            return False

class IdlDictionaryMember(TypedObject):
    def __init__(self, node):
        self.default_value = None
        self.extended_attributes = {}
        self.idl_type = None
        self.is_required = bool(node.GetProperty('REQUIRED'))
        self.name = node.GetName()
        # ExtendAttr: BooleanOnly
        self.value_only = None
        
        for child in node.GetChildren():
            child_class = child.GetClass()
            if child_class == 'Type':
                self.idl_type = type_node_to_type(child)
            elif child_class == 'Default':
                self.default_value = default_node_to_idl_literal(child)
            elif child_class == 'ExtAttributes':
                self.extended_attributes = (
                    ext_attributes_node_to_extended_attributes(child))
            else:
                raise ValueError('Unrecognized node class: %s' % child_class)

        if self.extended_attributes.get('BooleanOnly'):
            if self.extended_attributes.get('BooleanOnly') == 'true':
                self.value_only = 'true'
            elif self.extended_attributes.get('BooleanOnly') == 'false':
                self.value_only = 'false'
            else:
                raise Exception(f"Not support BooleanOnly value in {self.name}.")
        
        self.exclude_id = self.extended_attributes.get('Exclude', '')

        # 当成员类型为数字类型时该扩展属性有效
        self.number_range:NumberRange = None
        if 'NumberRange' in self.extended_attributes and self.idl_type.is_numeric_type:
            raw_value = self.extended_attributes['NumberRange']
            if self.idl_type.is_integer_type:
                self.number_range = NumberRange.from_string(raw_value, is_float=False)
            elif self.idl_type.is_floating_type:
                self.number_range = NumberRange.from_string(raw_value, is_float=True)
            assert self.number_range
        
        self.number_enum:list = []
        if 'NumberEnum' in self.extended_attributes:
            raw_value = self.extended_attributes['NumberEnum']
            self.number_enum = ProcessNumberEnum(self.idl_type, raw_value)

    def accept(self, visitor):
        visitor.visit_dictionary_member(self)

    def __eq__(self, other):
        return (
            self.name == other.name
            and self.idl_type.name == other.idl_type.name
        )

################################################################################
# Enumerations
################################################################################


class IdlEnum(object):
    def __init__(self, node):
        self.name = node.GetName()
        self.idl_type = IdlType(self.name)
        self.values = []
        for child in node.GetChildren():
            self.values.append(child.GetName())

    def accept(self, visitor):
        visitor.visit_enumeration(self)

    def get(self):
        '''
            Return a random enumration.
        '''
        return random.choice(self.values)
    
    def merge(self, other):
        self.values.extend(other.values)
        self.values = list(set(self.values))

################################################################################
# Typedefs
################################################################################
class IdlTypedef(object):
    idl_type_attributes = ('idl_type', )

    def __init__(self, node):
        self.name = node.GetName()
        # set all idl_type to IdlUnionType
        parse_type = typedef_node_to_type(node)
        if parse_type.is_union_type:
            self.idl_type = parse_type
        else:
            self.idl_type = IdlUnionType([parse_type])

    def accept(self, visitor):
        visitor.visit_typedef(self)

    def merge(self, other):
        # merge another IdlTypedef, covert idl_type to IdlUnionType
        self.idl_type.merge(other.idl_type)

################################################################################
# Arguments
################################################################################
class IdlArgument(TypedObject):
    def __init__(self, node=None):
        self.extended_attributes = {}
        self.idl_type:IdlType = None
        self.is_optional = False  # syntax: (optional T)
        self.is_variadic = False  # syntax: (T...)
        self.default_value = None

        if not node:
            return

        self.is_optional = node.GetProperty('OPTIONAL')
        self.name = node.GetName()

        children = node.GetChildren()
        for child in children:
            child_class = child.GetClass()
            if child_class == 'Type':
                self.idl_type = type_node_to_type(child)
            elif child_class == 'ExtAttributes':
                self.extended_attributes = ext_attributes_node_to_extended_attributes(
                    child)
            elif child_class == 'Argument':
                child_name = child.GetName()
                if child_name != '...':
                    raise ValueError(
                        'Unrecognized Argument node; expected "...", got "%s"'
                        % child_name)
                self.is_variadic = bool(child.GetProperty('ELLIPSIS'))
            elif child_class == 'Default':
                self.default_value = default_node_to_idl_literal(child)
            else:
                raise ValueError('Unrecognized node class: %s' % child_class)
        
        # 这个属性代表该参数来源只能是方法所在接口
        # 比如：存在一个RTCPeerConnection变量pc1，pc1.setLocalDescription参数来源只能是pc1.createOffer
        #      或者pc1.createAnswer
        if 'FromThis' in self.extended_attributes:
            self.arg_from = 'this'
        # 这个属性代表该参数来源只能是方法所在的其他接口
        # 比如：存在两个RTCPeerConnection变量pc1和pc2，pc1.setRemoteDescription参数来源只能是pc2.createOffer
        #      或者pc2.createAnswer
        elif 'FromOther' in self.extended_attributes:
            self.arg_from = 'other'
        else:
            self.arg_from = ''

        # 当参数类型为数字类型时该扩展属性有效
        self.number_range:tuple = tuple()
        # 考虑开闭区间
        if 'NumberRange' in self.extended_attributes and self.idl_type.is_numeric_type:
            raw_value = self.extended_attributes['NumberRange']
            if self.idl_type.is_integer_type:
                self.number_range = NumberRange.from_string(raw_value, is_float=False)
            elif self.idl_type.is_floating_type:
                self.number_range = NumberRange.from_string(raw_value, is_float=True)
            assert self.number_range
        
        # 数字类型的固定枚举值，如果存在，那么在生成数字时会优先使用枚举值
        self.number_enum:list = []
        if 'NumberEnum' in self.extended_attributes:
            raw_value = self.extended_attributes['NumberEnum']
            self.number_enum = ProcessNumberEnum(self.idl_type, raw_value)


    def accept(self, visitor):
        visitor.visit_argument(self)

    def __eq__(self, other):
        return (
            self.idl_type.name == other.idl_type.name
            and self.name == other.name
        )
    
    def __repr__(self):
        return f"{self.idl_type.name} {self.name}"

################################################################################
# Operations
################################################################################
class IdlOperation(TypedObject):
    def __init__(self, node=None):
        self.arguments:List[IdlArgument] = []
        self.extended_attributes = {}
        self.specials = []
        self.is_constructor = False
        self.idl_type = None
        self.is_static = False
        # In what interface the attribute is (originally) defined when the
        # attribute is inherited from an ancestor interface.
        self.defined_in:IdlInterface = None
        self.call_after = []
        # wait表示当前operation是否在等待调用条件满足
        self.wait = False

        if not node:
            return
        self.node = node
        if self.node.GetProperties().get('GETTER') is True:
            self.is_getter = True
        else:
            self.is_getter = False
        
        if self.node.GetProperties().get('SETTER') is True:
            self.is_setter = True
        else:
            self.is_setter = False

        self.name = node.GetName()
        self.is_clone = self.name == 'clone'

        self.is_static = bool(node.GetProperty('STATIC'))
        property_dictionary = node.GetProperties()
        for special_keyword in SPECIAL_KEYWORD_LIST:
            if special_keyword in property_dictionary:
                self.specials.append(special_keyword.lower())

        children = node.GetChildren()
        for child in children:
            child_class = child.GetClass()
            if child_class == 'Arguments':
                self.arguments = arguments_node_to_arguments(child)
            elif child_class == 'Type':
                self.idl_type = type_node_to_type(child)
            elif child_class == 'ExtAttributes':
                self.extended_attributes = ext_attributes_node_to_extended_attributes(
                    child)
            else:
                raise ValueError('Unrecognized node class: %s' % child_class)

        if 'Unforgeable' in self.extended_attributes and self.is_static:
            raise ValueError(
                '[Unforgeable] cannot appear on static operations.')

        if 'CallAfter' in self.extended_attributes:
            raw_text = self.extended_attributes['CallAfter']
            self.call_after = raw_text[1:-1].replace(' ', '').split(',')

        # 调用几率，范围设定为[0, 10)，不支持浮点数
        self.weight = int(self.extended_attributes.get('Weight', '10'))

    def __repr__(self):
        return f"{self.idl_type.name} {self.defined_in.name}.{self.name}({', '.join(arg.idl_type.name for arg in self.arguments)})"

    def __str__(self):
        return self.__repr__()

    def __hash__(self):
        return hash(str([self.name, self.defined_in.name, self.arguments]))

    def __eq__(self, other):
        '''
            * name
            * number of arguments
            * argument
        '''
        if self.name != other.name:
            return False
        
        if len(self.arguments) != len(other.arguments):
            return False
        
        for i in range(0, len(self.arguments)):
            if self.arguments[i] != other.arguments[i]:
                return False
        
        return True

    @classmethod
    def constructor_from_arguments_node(cls, name, arguments_node):
        constructor = cls()
        constructor.name = name
        constructor.arguments = arguments_node_to_arguments(arguments_node)
        constructor.is_constructor = True
        return constructor

    def accept(self, visitor):
        visitor.visit_operation(self)
        for argument in self.arguments:
            argument.accept(visitor)

    def has_arg(self, arg_type:str):
        for arg in self.arguments:
            if arg.idl_type.is_nested and arg.idl_type.has_type(arg_type):
                return True
            elif arg.idl_type.name == arg_type:
                return True
        return False

    def has_ret(self, ret_type:str):
        if self.idl_type.is_nested and self.idl_type.has_type(ret_type):
            return True
        elif self.idl_type.name == ret_type:
            return True
        return False

################################################################################
# Interfaces
################################################################################
class IdlInterface(object):
    def __init__(self, node):
        self.node                           = node
        self.attributes:list[IdlAttribute]  = []
        self.attributes_dict:dict[str, IdlAttribute] = {}
        self.attributes_type_dict:dict[str, IdlAttribute] = {}
        self.constants                      = []
        self.constructors                   = []
        self.custom_constructors            = []
        self.extended_attributes            = {}
        self.operations:list[IdlOperation]  = []
        self.operations_dict                = {}

        # 处理AST时parent为str，经过IdlContext处理之后会修正为IdlInterface
        self.parent:Union[str, IdlInterface] = None
        self.stringifier                    = None
        self.iterable                       = None
        self.has_indexed_elements           = False
        self.has_named_property_getter      = False
        self.maplike                        = None
        self.setlike                        = None
        self.original_interface             = None
        self.partial_interfaces             = []

        self.is_callback                    = bool(node.GetProperty('CALLBACK'))
        self.event_handler                  = None
        self.is_partial                     = bool(node.GetProperty('PARTIAL'))
        self.is_mixin                       = bool(node.GetProperty('MIXIN'))
        self.name:str                           = node.GetName()
        self.idl_type:IdlType                       = IdlType(self.name)

        self.eventhandlers                  = []

        has_indexed_property_getter         = False
        has_integer_typed_length            = False

        # These are used to support both constructor operations and old style
        # [Constructor] extended attributes. Ideally we should do refactoring
        # for constructor code generation but we will use a new code generator
        # soon so this kind of workaround should be fine.
        constructor_operations              = []
        custom_constructor_operations       = []
        constructor_operations_extended_attributes = {}

        def is_invalid_attribute_type(idl_type):
            return idl_type.is_callback_function or \
                idl_type.is_dictionary or \
                idl_type.is_record_type or \
                idl_type.is_sequence_type

        children = node.GetChildren()
        for child in children:
            child_class = child.GetClass()
            if child_class == 'Attribute':
                attr = IdlAttribute(child)
                if is_invalid_attribute_type(attr.idl_type):
                    raise ValueError(
                        'Type "%s" cannot be used as an attribute.' %
                        attr.idl_type)
                if attr.idl_type.is_integer_type and attr.name == 'length':
                    has_integer_typed_length = True
                attr.defined_in = self
                self.attributes.append(attr)
                if not self.attributes_type_dict.get(attr.idl_type.name):
                    self.attributes_type_dict[attr.idl_type.name] = []
                self.attributes_dict[attr.name] = attr # 属性不可能重名
                self.attributes_type_dict[attr.idl_type.name].append(attr)
                if attr.is_eventhandler:
                    self.eventhandlers.append(attr)
            elif child_class == 'Const':
                self.constants.append(IdlConstant(child))
            elif child_class == 'ExtAttributes':
                extended_attributes = ext_attributes_node_to_extended_attributes(
                    child)
                self.constructors, self.custom_constructors = (
                    extended_attributes_to_constructors(extended_attributes))
                clear_constructor_attributes(extended_attributes)
                self.extended_attributes = extended_attributes
            elif child_class == 'Operation':
                op = IdlOperation(child)
                if 'getter' in op.specials:
                    if str(op.arguments[0].idl_type) == 'unsigned long':
                        has_indexed_property_getter = True
                    elif str(op.arguments[0].idl_type) == 'DOMString':
                        self.has_named_property_getter = True
                # find handleEvent operation
                if op.name == 'handleEvent':
                    if self.event_handler:
                        raise Exception(f"Duplicate handleEvent for {self.name}")
                    self.event_handler = op
                op.defined_in = self
                self.operations.append(op)

            elif child_class == 'Constructor':
                operation = constructor_operation_from_node(child)
                if operation.is_custom:
                    custom_constructor_operations.append(operation.constructor)
                else:
                    # Check extended attributes consistency when we previously
                    # handle constructor operations.
                    if constructor_operations:
                        check_constructor_operations_extended_attributes(
                            constructor_operations_extended_attributes,
                            operation.extended_attributes)
                    constructor_operations.append(operation.constructor)
                    constructor_operations_extended_attributes.update(
                        operation.extended_attributes)
            elif child_class == 'Inherit':
                self.parent = child.GetName()
            elif child_class == 'Stringifier':
                self.stringifier = IdlStringifier(child)
                self.process_stringifier()
            elif child_class == 'Iterable':
                self.iterable = IdlIterable(child)
            elif child_class == 'Maplike':
                self.maplike = IdlMaplike(child)
            elif child_class == 'Setlike':
                self.setlike = IdlSetlike(child)
            else:
                raise ValueError('Unrecognized node class: %s' % child_class)

        if len(list(filter(None, [self.iterable, self.maplike, self.setlike]))) > 1:
            raise ValueError(
                'Interface can only have one of iterable<>, maplike<> and setlike<>.'
            )

        # TODO(rakuco): This validation logic should be in v8_interface according to bashi@.
        # At the moment, doing so does not work because several IDL files are partial Window
        # interface definitions, and interface_dependency_resolver.py doesn't seem to have any logic
        # to prevent these partial interfaces from resetting has_named_property to False.
        if 'LegacyUnenumerableNamedProperties' in self.extended_attributes and \
           not self.has_named_property_getter:
            raise ValueError(
                '[LegacyUnenumerableNamedProperties] can be used only in interfaces '
                'that support named properties.')

        if has_integer_typed_length and has_indexed_property_getter:
            self.has_indexed_elements = True
        else:
            if self.iterable is not None and self.iterable.key_type is None:
                raise ValueError(
                    'Value iterators (iterable<V>) must be accompanied by an indexed '
                    'property getter and an integer-typed length attribute.')

        if 'Unforgeable' in self.extended_attributes:
            raise ValueError('[Unforgeable] cannot appear on interfaces.')

        if constructor_operations or custom_constructor_operations:
            if self.constructors or self.custom_constructors:
                raise ValueError('Detected mixed [Constructor] and consructor '
                                 'operations. Do not use both in a single '
                                 'interface.')
            extended_attributes = (
                convert_constructor_operations_extended_attributes(
                    constructor_operations_extended_attributes))
            if any(name in extended_attributes.keys()
                   for name in self.extended_attributes.keys()):
                raise ValueError('Detected mixed extended attributes for '
                                 'both [Constructor] and constructor '
                                 'operations. Do not use both in a single '
                                 'interface')
            self.constructors = constructor_operations
            self.custom_constructors = custom_constructor_operations
            self.extended_attributes.update(extended_attributes)

    def accept(self, visitor):
        visitor.visit_interface(self)
        for attribute in self.attributes:
            attribute.accept(visitor)
        for constant in self.constants:
            constant.accept(visitor)
        for constructor in self.constructors:
            constructor.accept(visitor)
        for custom_constructor in self.custom_constructors:
            custom_constructor.accept(visitor)
        for operation in self.operations:
            operation.accept(visitor)
        if self.iterable:
            self.iterable.accept(visitor)
        elif self.maplike:
            self.maplike.accept(visitor)
        elif self.setlike:
            self.setlike.accept(visitor)

    def process_stringifier(self):
        """Add the stringifier's attribute or named operation child, if it has
        one, as a regular attribute/operation of this interface."""
        if self.stringifier.attribute:
            self.attributes.append(self.stringifier.attribute)
        elif self.stringifier.operation:
            self.operations.append(self.stringifier.operation)

    def has_attr(self, attr_name:str):
        return attr_name in self.attributes_dict

    def has_type_attr(self, attr_type:str):
        return attr_type in self.attributes_type_dict

    def update_attribute(self, attr:IdlAttribute):
        self.attributes.append(attr)
        self.attributes_dict[attr.name] = attr
        if not self.attributes_type_dict.get(attr.idl_type.name):
            self.attributes_type_dict[attr.idl_type.name] = []
        self.attributes_type_dict[attr.idl_type.name].append(attr)

    def merge(self, other:IdlInterface):
        """Merge in another interface's members (e.g., partial interface)"""
        for attr in other.attributes:
            self.update_attribute(attr)
        # self.attributes.extend(other.attributes)
        # self.attributes_dict.update(other.attributes_dict)
        # for k,v in other.attributes_type_dict.items():
        #     if not self.attributes_type_dict.get(k):
        #         self.attributes_type_dict[k] = []
        #     self.attributes_type_dict[k].extend(v)

        self.constants.extend(other.constants)
        for op in other.operations:
            # op.defined_in = self # TODO: 应该在defined_in中记录下父类的信息
            self.operations.append(op)
        self.constructors.extend(other.constructors)
        self.eventhandlers.extend(other.eventhandlers)
        
        for k, v in other.extended_attributes.items():
            if not self.extended_attributes.get(k):
                self.extended_attributes[k] = v
            elif k == 'Exposed':
                self.extended_attributes[k] = list(set(
                    self.extended_attributes[k] + v
                ))
            # TODO: 合并时处理其余扩展属性
            elif v != self.extended_attributes[k]:
                # error_msg = f"Partial interface {self.name} has different extended attribute {k}: {v} | {self.extended_attributes[k]}"
                # print(error_msg)
                # raise Exception(error_msg)
                pass

        if self.stringifier is None:
            self.stringifier = other.stringifier

    def inherite(self, parent:IdlInterface):
        self.eventhandlers.extend(parent.eventhandlers)

        for attr in parent.attributes:
            # 不继承重名的属性
            if self.has_attr(attr.name):
                continue
            self.update_attribute(attr)
        
        # 支持重写父类方法
        for op in parent.operations:
            overwrite = False
            for exist_op in self.operations:
                if op.name == exist_op.name:
                    overwrite = True
                    break
            if not overwrite:
                # op.defined_in = self
                self.operations.append(op)
        
        self.constants.extend(parent.constants)
        for k, v in parent.extended_attributes.items():
            if not self.extended_attributes.get(k):
                self.extended_attributes[k] = v
            elif k == 'Exposed':
                self.extended_attributes[k] = list(set(
                    self.extended_attributes[k] + v
                ))
            elif v != self.extended_attributes[k]:
                pass

        if self.stringifier is None:
            self.stringifier = parent.stringifier

    def operations_to_dict(self):
        for op in self.operations:
            ops = self.operations_dict.get(op.name)
            if ops:
                ops.append(op)
            else:
                self.operations_dict[op.name] = [op]

    def is_subclass_of(self, maybe_parent:Union[str, IdlInterface]) -> bool:
        '''判断某个接口是否为本接口的基类,为了准确表达语义,两接口相等时返回False'''
        i = self.parent
        while i:
            assert type(i) is IdlInterface # 只有在parent被修正为IdlInterface时才可使用此方法
            if type(maybe_parent) is str:
                parent_info = i.name
            else:
                parent_info = i
            if maybe_parent == parent_info:
                return True
            i = i.parent
        return False
    
    def parent_name(self) -> str:
        if type(self.parent) is str:
            return self.parent
        elif type(self.parent) is IdlInterface:
            return self.parent.name
        else:
            raise Exception(f"Wrong parent type of interface {self.name}")

################################################################################
# Attributes
################################################################################
class IdlAttribute(TypedObject):
    def __init__(self, node=None):
        self.is_read_only = bool(
            node.GetProperty('READONLY')) if node else False
        self.is_static = bool(node.GetProperty('STATIC')) if node else False
        self.name = node.GetName() if node else None
        self.idl_type = None
        self.extended_attributes = {}
        # In what interface the attribute is (originally) defined when the
        # attribute is inherited from an ancestor interface.
        self.defined_in = None

        if node:
            children = node.GetChildren()
            for child in children:
                child_class = child.GetClass()
                if child_class == 'Type':
                    self.idl_type = type_node_to_type(child)
                elif child_class == 'ExtAttributes':
                    self.extended_attributes = ext_attributes_node_to_extended_attributes(
                        child)
                else:
                    raise ValueError(
                        'Unrecognized node class: %s' % child_class)

        if 'Unforgeable' in self.extended_attributes and self.is_static:
            raise ValueError(
                '[Unforgeable] cannot appear on static attributes.')
        
        if 'EventHandler' in self.extended_attributes:
            self.is_eventhandler = True
            if 'EventType' in self.extended_attributes:
                self.event_type = self.extended_attributes['EventType']
            else:
                self.event_type = ''
        else:
            self.is_eventhandler = False
        
        self.number_range:NumberRange = None
        if 'NumberRange' in self.extended_attributes and self.idl_type.is_numeric_type:
            raw_value = self.extended_attributes['NumberRange']
            if self.idl_type.is_integer_type:
                self.number_range = NumberRange.from_string(raw_value, is_float=False)
            elif self.idl_type.is_floating_type:
                self.number_range = NumberRange.from_string(raw_value, is_float=True)
            assert self.number_range
        
        self.number_enum:list = []
        if 'NumberEnum' in self.extended_attributes:
            raw_value = self.extended_attributes['NumberEnum']
            self.number_enum = ProcessNumberEnum(self.idl_type, raw_value)

    def accept(self, visitor):
        visitor.visit_attribute(self)

    def __repr__(self):
        return f"({self.idl_type.name}){self.defined_in.name}.{self.name}"

    def __str__(self):
        return self.__repr__()

    def __eq__(self, other):
        if self.idl_type.name != other.idl_type.name:
            return False
        if self.name != other.name:
            return False
        
        # if self.defined_in and other.defined_in:
        #     if self.defined_in.name != other.defined_in.name:
        #         return False
        return True

################################################################################
# Constants
################################################################################


class IdlConstant(TypedObject):
    def __init__(self, node):
        children = node.GetChildren()
        num_children = len(children)
        if num_children < 2 or num_children > 3:
            raise ValueError('Expected 2 or 3 children, got %s' % num_children)
        type_node = children[0]
        value_node = children[1]
        value_node_class = value_node.GetClass()
        if value_node_class != 'Value':
            raise ValueError('Expected Value node, got %s' % value_node_class)

        self.name = node.GetName()
        # ConstType is more limited than Type, so subtree is smaller and
        # we don't use the full type_node_to_type function.
        self.idl_type = type_node_inner_to_type(type_node)
        self.value = value_node.GetProperty('VALUE')
        # In what interface the attribute is (originally) defined when the
        # attribute is inherited from an ancestor interface.
        self.defined_in = None

        if num_children == 3:
            ext_attributes_node = children[2]
            self.extended_attributes = ext_attributes_node_to_extended_attributes(
                ext_attributes_node)
        else:
            self.extended_attributes = {}

    def accept(self, visitor):
        visitor.visit_constant(self)


################################################################################
# Literals
################################################################################


class IdlLiteral(object):
    def __init__(self, idl_type, value):
        self.idl_type = idl_type
        self.value = value
        self.is_null = False

    def __str__(self):
        if self.idl_type == 'DOMString':
            if self.value:
                return '"%s"' % self.value
            else:
                return 'WTF::g_empty_string'
        if self.idl_type == 'integer':
            return '%d' % self.value
        if self.idl_type == 'float':
            return '%g' % self.value
        if self.idl_type == 'boolean':
            return 'true' if self.value else 'false'
        if self.idl_type == 'dictionary':
            return self.value
        raise ValueError('Unsupported literal type: %s' % self.idl_type)


class IdlLiteralNull(IdlLiteral):
    def __init__(self):
        self.idl_type = 'NULL'
        self.value = None
        self.is_null = True

    def __str__(self):
        return 'nullptr'


def default_node_to_idl_literal(node):
    idl_type = node.GetProperty('TYPE')
    value = node.GetProperty('VALUE')
    if idl_type == 'DOMString':
        if '"' in value or '\\' in value:
            raise ValueError('Unsupported string value: %r' % value)
        return IdlLiteral(idl_type, value)
    if idl_type == 'integer':
        return IdlLiteral(idl_type, int(value, base=0))
    if idl_type == 'float':
        return IdlLiteral(idl_type, float(value))
    if idl_type in ['boolean', 'sequence']:
        return IdlLiteral(idl_type, value)
    if idl_type == 'NULL':
        return IdlLiteralNull()
    if idl_type == 'dictionary':
        return IdlLiteral(idl_type, value)
    raise ValueError('Unrecognized default value type: %s' % idl_type)


################################################################################
# Stringifiers
################################################################################


class IdlStringifier(object):
    def __init__(self, node):
        self.attribute = None
        self.operation = None
        self.extended_attributes = {}

        for child in node.GetChildren():
            child_class = child.GetClass()
            if child_class == 'Attribute':
                self.attribute = IdlAttribute(child)
            elif child_class == 'Operation':
                operation = IdlOperation(child)
                if operation.name:
                    self.operation = operation
            elif child_class == 'ExtAttributes':
                self.extended_attributes = ext_attributes_node_to_extended_attributes(
                    child)
            else:
                raise ValueError('Unrecognized node class: %s' % child_class)

        # Copy the stringifier's extended attributes (such as [Unforgable]) onto
        # the underlying attribute or operation, if there is one.
        if self.attribute or self.operation:
            (self.attribute or self.operation).extended_attributes.update(
                self.extended_attributes)


################################################################################
# Iterable, Maplike, Setlike
################################################################################


class IdlIterableOrMaplikeOrSetlike(TypedObject):
    def __init__(self, node):
        self.extended_attributes = {}
        self.type_children = []

        for child in node.GetChildren():
            child_class = child.GetClass()
            if child_class == 'ExtAttributes':
                self.extended_attributes = ext_attributes_node_to_extended_attributes(
                    child)
            elif child_class == 'Type':
                self.type_children.append(child)
            else:
                raise ValueError('Unrecognized node class: %s' % child_class)


class IdlIterable(IdlIterableOrMaplikeOrSetlike):
    idl_type_attributes = ('key_type', 'value_type')

    def __init__(self, node):
        super(IdlIterable, self).__init__(node)

        if len(self.type_children) == 1:
            self.key_type = None
            self.value_type = type_node_to_type(self.type_children[0])
        elif len(self.type_children) == 2:
            self.key_type = type_node_to_type(self.type_children[0])
            self.value_type = type_node_to_type(self.type_children[1])
        else:
            raise ValueError('Unexpected number of type children: %d' % len(
                self.type_children))
        del self.type_children

    def accept(self, visitor):
        visitor.visit_iterable(self)


class IdlMaplike(IdlIterableOrMaplikeOrSetlike):
    idl_type_attributes = ('key_type', 'value_type')

    def __init__(self, node):
        super(IdlMaplike, self).__init__(node)

        self.is_read_only = bool(node.GetProperty('READONLY'))

        if len(self.type_children) == 2:
            self.key_type = type_node_to_type(self.type_children[0])
            self.value_type = type_node_to_type(self.type_children[1])
        else:
            raise ValueError(
                'Unexpected number of children: %d' % len(self.type_children))
        del self.type_children

    def accept(self, visitor):
        visitor.visit_maplike(self)


class IdlSetlike(IdlIterableOrMaplikeOrSetlike):
    idl_type_attributes = ('value_type', )

    def __init__(self, node):
        super(IdlSetlike, self).__init__(node)

        self.is_read_only = bool(node.GetProperty('READONLY'))

        if len(self.type_children) == 1:
            self.value_type = type_node_to_type(self.type_children[0])
        else:
            raise ValueError(
                'Unexpected number of children: %d' % len(self.type_children))
        del self.type_children

    def accept(self, visitor):
        visitor.visit_setlike(self)


################################################################################
# Includes statements
################################################################################


class IdlIncludes(object):
    def __init__(self, node):
        self.interface = node.GetName()
        self.mixin = node.GetProperty('REFERENCE')

    def accept(self, visitor):
        visitor.visit_include(self)


################################################################################
# Extended attributes
################################################################################


class Exposure:
    """An Exposure holds one Exposed or RuntimeEnabled condition.
    Each exposure has two properties: exposed and runtime_enabled.
    Exposure(e, r) corresponds to [Exposed(e r)]. Exposure(e) corresponds to
    [Exposed=e].
    """

    def __init__(self, exposed, runtime_enabled=None):
        self.exposed = exposed
        self.runtime_enabled = runtime_enabled

    def __str__(self):
        return self.exposed

    def __repr__(self):
        return self.exposed

def ext_attributes_node_to_extended_attributes(node):
    """
    Returns:
      Dictionary of {ExtAttributeName: ExtAttributeValue}.
      Value is usually a string, with these exceptions:
      Constructors: value is a list of Arguments nodes, corresponding to
        possible signatures of the constructor.
      CustomConstructors: value is a list of Arguments nodes, corresponding to
        possible signatures of the custom constructor.
      NamedConstructor: value is a Call node, corresponding to the single
        signature of the named constructor.
    """
    # Primarily just make a dictionary from the children.
    # The only complexity is handling various types of constructors:
    # Constructors and Custom Constructors can have duplicate entries due to
    # overloading, and thus are stored in temporary lists.
    # However, Named Constructors cannot be overloaded, and thus do not have
    # a list.
    # TODO(bashi): Remove |constructors| and |custom_constructors|.
    constructors = []
    custom_constructors = []
    extended_attributes = {}

    def child_node(extended_attribute_node):
        children = extended_attribute_node.GetChildren()
        if not children:
            return None
        if len(children) > 1:
            raise ValueError(
                'ExtAttributes node with %s children, expected at most 1' %
                len(children))
        return children[0]

    extended_attribute_node_list = node.GetChildren()
    for extended_attribute_node in extended_attribute_node_list:
        name = extended_attribute_node.GetName()
        child = child_node(extended_attribute_node)
        child_class = child and child.GetClass()
        if name == 'Constructor':
            raise ValueError('[Constructor] is deprecated. Use constructor '
                             'operations')
        elif name == 'CustomConstructor':
            raise ValueError('[CustomConstructor] is deprecated. Use '
                             'constructor operations with [Custom]')
        elif name == 'NamedConstructor':
            if child_class and child_class != 'Call':
                raise ValueError(
                    '[NamedConstructor] only supports Call as child, but has child of class: %s'
                    % child_class)
            extended_attributes[name] = child
        elif name == 'Exposed':
            if child_class and child_class != 'Arguments':
                raise ValueError(
                    '[Exposed] only supports Arguments as child, but has child of class: %s'
                    % child_class)
            exposures = []
            if child_class == 'Arguments':
                exposures = [
                    Exposure(
                        exposed=str(arg.idl_type), runtime_enabled=arg.name)
                    for arg in arguments_node_to_arguments(child)
                ]
            else:
                value = extended_attribute_node.GetProperty('VALUE')
                if type(value) is str:
                    exposures = [Exposure(exposed=value)]
                else:
                    exposures = [Exposure(exposed=v) for v in value]
            extended_attributes[name] = exposures
        elif child:
            raise ValueError(
                'ExtAttributes node with unexpected children: %s' % name)
        else:
            value = extended_attribute_node.GetProperty('VALUE')
            extended_attributes[name] = value

    # Store constructors and custom constructors in special list attributes,
    # which are deleted later. Note plural in key.
    if constructors:
        extended_attributes['Constructors'] = constructors
    if custom_constructors:
        extended_attributes['CustomConstructors'] = custom_constructors

    return extended_attributes


def extended_attributes_to_constructors(extended_attributes):
    """Returns constructors and custom_constructors (lists of IdlOperations).

    Auxiliary function for IdlInterface.__init__.
    """

    # TODO(bashi): Remove 'Constructors' and 'CustomConstructors'.

    constructor_list = extended_attributes.get('Constructors', [])
    constructors = [
        IdlOperation.constructor_from_arguments_node('Constructor',
                                                     arguments_node)
        for arguments_node in constructor_list
    ]

    custom_constructor_list = extended_attributes.get('CustomConstructors', [])
    custom_constructors = [
        IdlOperation.constructor_from_arguments_node('CustomConstructor',
                                                     arguments_node)
        for arguments_node in custom_constructor_list
    ]

    if 'NamedConstructor' in extended_attributes:
        # FIXME: support overloaded named constructors, and make homogeneous
        #name = 'NamedConstructor'
        call_node = extended_attributes['NamedConstructor']
        extended_attributes['NamedConstructor'] = call_node.GetName()
        children = call_node.GetChildren()
        if len(children) != 1:
            raise ValueError('NamedConstructor node expects 1 child, got %s.' %
                             len(children))
        arguments_node = children[0]
        named_constructor = IdlOperation.constructor_from_arguments_node(
            'NamedConstructor', arguments_node)
        # FIXME: should return named_constructor separately; appended for Perl
        constructors.append(named_constructor)

    return constructors, custom_constructors


class ConstructorOperation(object):
    """Represents a constructor operation. This is a tentative object used to
    create constructors in IdlInterface.
    """

    def __init__(self, constructor, extended_attributes, is_custom):
        self.constructor = constructor
        self.extended_attributes = extended_attributes
        self.is_custom = is_custom


def constructor_operation_from_node(node):
    """Creates a ConstructorOperation from the given |node|.
    """

    arguments_node = None
    extended_attributes = {}

    for child in node.GetChildren():
        child_class = child.GetClass()
        if child_class == 'Arguments':
            arguments_node = child
        elif child_class == 'ExtAttributes':
            extended_attributes = ext_attributes_node_to_extended_attributes(
                child)
        else:
            raise ValueError('Unrecognized node class: %s' % child_class)

    if not arguments_node:
        raise ValueError('Expected Arguments node for constructor operation')

    if 'Custom' in extended_attributes:
        if extended_attributes['Custom']:
            raise ValueError('[Custom] should not have a value on constructor '
                             'operations')
        del extended_attributes['Custom']
        constructor = IdlOperation.constructor_from_arguments_node(
            'CustomConstructor', arguments_node)
        return ConstructorOperation(
            constructor, extended_attributes, is_custom=True)
    else:
        constructor = IdlOperation.constructor_from_arguments_node(
            'Constructor', arguments_node)
        return ConstructorOperation(
            constructor, extended_attributes, is_custom=False)


def check_constructor_operations_extended_attributes(current_attrs, new_attrs):
    """Raises a ValueError if two extended attribute lists have different values
    of constructor related attributes.
    """

    attrs_to_check = ['CallWith', 'RaisesException']
    for attr in attrs_to_check:
        if current_attrs.get(attr) != new_attrs.get(attr):
            raise ValueError('[{}] should have the same value on all '
                             'constructor operations'.format(attr))


def convert_constructor_operations_extended_attributes(extended_attributes):
    """Converts extended attributes specified on constructor operations to
    extended attributes for an interface definition (e.g. [ConstructorCallWith])
    """

    converted = {}
    for name, value in extended_attributes.items():
        if name == "CallWith":
            converted["ConstructorCallWith"] = value
        elif name == "RaisesException":
            if value:
                raise ValueError(
                    '[RaisesException] should not have a value on '
                    'constructor operations')
            converted["RaisesException"] = 'Constructor'
        elif name == "MeasureAs":
            converted["MeasureAs"] = value
        elif name == "Measure":
            converted["Measure"] = None
        else:
            raise ValueError(
                '[{}] is not supported on constructor operations'.format(name))

    return converted


def clear_constructor_attributes(extended_attributes):
    # Deletes Constructor*s* (plural), sets Constructor (singular)
    if 'Constructors' in extended_attributes:
        del extended_attributes['Constructors']
        extended_attributes['Constructor'] = None
    if 'CustomConstructors' in extended_attributes:
        del extended_attributes['CustomConstructors']
        extended_attributes['CustomConstructor'] = None


################################################################################
# Types
################################################################################


def type_node_to_type(node):
    children = node.GetChildren()
    if len(children) != 1 and len(children) != 2:
        raise ValueError(
            'Type node expects 1 or 2 child(ren), got %d.' % len(children))

    base_type = type_node_inner_to_type(children[0])
    if len(children) == 2:
        extended_attributes = ext_attributes_node_to_extended_attributes(
            children[1])
        base_type = IdlAnnotatedType(base_type, extended_attributes)

    if node.GetProperty('NULLABLE'):
        base_type = IdlNullableType(base_type)

    return base_type


def type_node_inner_to_type(node):
    node_class = node.GetClass()
    # Note Type*r*ef, not Typedef, meaning the type is an identifier, thus
    # either a typedef shorthand (but not a Typedef declaration itself) or an
    # interface type. We do not distinguish these, and just use the type name.
    if node_class in ['PrimitiveType', 'StringType', 'Typeref']:
        # unrestricted syntax: unrestricted double | unrestricted float
        is_unrestricted = bool(node.GetProperty('UNRESTRICTED'))
        return IdlType(node.GetName(), is_unrestricted=is_unrestricted)
    elif node_class == 'Any':
        return IdlType('any')
    elif node_class in ['Sequence', 'FrozenArray']:
        return sequence_node_to_type(node)
    elif node_class == 'UnionType':
        return union_type_node_to_idl_union_type(node)
    elif node_class == 'Promise':
        return promise_node_to_type(node)
        # idl_type = IdlType('Promise')
        # idl_type.node = node
        # return idl_type
    elif node_class == 'Record':
        return record_node_to_type(node)
    raise ValueError('Unrecognized node class: %s' % node_class)

def promise_node_to_type(node):
    member_types = [
        type_node_to_type(member_type_node)
        for member_type_node in node.GetChildren()
    ]
    return IdlPromiseType(member_types)

def record_node_to_type(node):
    children = node.GetChildren()
    if len(children) != 2:
        raise ValueError('record<K,V> node expects exactly 2 children, got %d'
                         % (len(children)))
    key_child = children[0]
    value_child = children[1]
    if key_child.GetClass() != 'StringType':
        raise ValueError('Keys in record<K,V> nodes must be string types.')
    if value_child.GetClass() != 'Type':
        raise ValueError('Unrecognized node class for record<K,V> value: %s' %
                         value_child.GetClass())
    return IdlRecordType(
        IdlType(key_child.GetName()), type_node_to_type(value_child))


def sequence_node_to_type(node):
    children = node.GetChildren()
    class_name = node.GetClass()
    if len(children) != 1:
        raise ValueError('%s node expects exactly 1 child, got %s' %
                         (class_name, len(children)))
    sequence_child = children[0]
    sequence_child_class = sequence_child.GetClass()
    if sequence_child_class != 'Type':
        raise ValueError('Unrecognized node class: %s' % sequence_child_class)
    element_type = type_node_to_type(sequence_child)
    if class_name == 'Sequence':
        sequence_type = IdlSequenceType(element_type)
    elif class_name == 'FrozenArray':
        sequence_type = IdlFrozenArrayType(element_type)
    else:
        raise ValueError('Unexpected node: %s' % class_name)
    if node.GetProperty('NULLABLE'):
        return IdlNullableType(sequence_type)
    return sequence_type


def typedef_node_to_type(node):
    children = node.GetChildren()
    if len(children) != 1:
        raise ValueError(
            'Typedef node with %s children, expected 1' % len(children))
    child = children[0]
    child_class = child.GetClass()
    if child_class != 'Type':
        raise ValueError('Unrecognized node class: %s' % child_class)
    return type_node_to_type(child)


def union_type_node_to_idl_union_type(node):
    member_types = [
        type_node_to_type(member_type_node)
        for member_type_node in node.GetChildren()
    ]
    return IdlUnionType(member_types)


################################################################################
# Visitor
################################################################################


class Visitor(object):
    """Abstract visitor class for IDL definitions traverse."""

    def visit_definitions(self, definitions):
        pass

    def visit_typed_object(self, typed_object):
        pass

    def visit_callback_function(self, callback_function):
        self.visit_typed_object(callback_function)

    def visit_dictionary(self, dictionary):
        pass

    def visit_dictionary_member(self, member):
        self.visit_typed_object(member)

    def visit_enumeration(self, enumeration):
        pass

    def visit_include(self, include):
        pass

    def visit_interface(self, interface):
        pass

    def visit_typedef(self, typedef):
        self.visit_typed_object(typedef)

    def visit_attribute(self, attribute):
        self.visit_typed_object(attribute)

    def visit_constant(self, constant):
        self.visit_typed_object(constant)

    def visit_operation(self, operation):
        self.visit_typed_object(operation)

    def visit_argument(self, argument):
        self.visit_typed_object(argument)

    def visit_iterable(self, iterable):
        self.visit_typed_object(iterable)

    def visit_maplike(self, maplike):
        self.visit_typed_object(maplike)

    def visit_setlike(self, setlike):
        self.visit_typed_object(setlike)
