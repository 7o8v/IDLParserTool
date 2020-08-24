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
"""Read an IDL file or complete IDL interface, producing an IdlDefinitions object.

Design doc:
http://www.chromium.org/developers/design-documents/idl-compiler#TOC-Front-end
"""

import os

from IDLParserTool.idl_parser.idl_parser import ParseFile as parse_file
from IDLParserTool.blink_idl_parser import BlinkIDLParser
from IDLParserTool.idl_definitions import IdlDefinitions
from IDLParserTool.idl_validator import EXTENDED_ATTRIBUTES_RELATIVE_PATH, IDLInvalidExtendedAttributeError, IDLExtendedAttributeValidator
from IDLParserTool.interface_dependency_resolver import InterfaceDependencyResolver
from IDLParserTool.utilities import idl_filename_to_component
from IDLParserTool.utilities import to_snake_case


def validate_blink_idl_definitions(idl_filename, idl_file_basename,
                                   definitions):
    """Validate file contents with filename convention.

       The Blink IDL conventions are:
       - If an IDL file defines an interface or a dictionary,
         the IDL file must contain exactly one definition. The definition
         name must agree with the file's basename, unless it is a partial
         definition. (e.g., 'partial interface Foo' can be in FooBar.idl).
       - An IDL file can contain typedefs and enums without having other
         definitions. There is no filename convention in this case.
       - Otherwise, an IDL file is invalid.
    """
    targets = (list(definitions.interfaces.values()) + list(definitions.dictionaries.values()))
    number_of_targets = len(targets)
    if number_of_targets > 1:
        raise Exception(
            'Expected exactly 1 definition in file {0}, but found {1}'.format(
                idl_filename, number_of_targets))
    if number_of_targets == 0:
        number_of_definitions = (len(definitions.enumerations) + len(
            definitions.typedefs) + len(definitions.callback_functions))
        if number_of_definitions == 0:
            raise Exception('No definition found in %s. (Missing semicolon?)' %
                            idl_filename)
        return
    target = targets[0]
    if target.is_partial:
        return
    if (target.name != idl_file_basename
            and to_snake_case(target.name) != idl_file_basename):
        raise Exception(
            'Definition name "{0}" disagrees with IDL file basename "{1}".'.
            format(target.name, idl_file_basename))


class IdlReader(object):
    def __init__(self, interfaces_info=None, outputdir=''):
        self.extended_attribute_validator = IDLExtendedAttributeValidator()
        self.interfaces_info = interfaces_info

        if interfaces_info:
            self.interface_dependency_resolver = InterfaceDependencyResolver(
                interfaces_info, self)
        else:
            self.interface_dependency_resolver = None

        self.parser = BlinkIDLParser(outputdir=outputdir)

    def read_idl_definitions(self, idl_filename):
        """Returns a dictionary whose key is component and value is an IdlDefinitions object for an IDL file, including all dependencies."""
        definitions = self.read_idl_file(idl_filename)
        component = idl_filename_to_component(idl_filename)

        if not self.interface_dependency_resolver:
            return {component: definitions}

        # This definitions should have a dictionary. No need to resolve any
        # dependencies.
        if not definitions.interfaces:
            return {component: definitions}

        return self.interface_dependency_resolver.resolve_dependencies(
            definitions, component)

    def read_idl_file(self, idl_filename):
        """Returns an IdlDefinitions object for an IDL file, without any dependencies.

        The IdlDefinitions object is guaranteed to contain a single
        IdlInterface; it may also contain other definitions, such as
        callback functions and enumerations."""
        ast = parse_file(self.parser, idl_filename)
        if not ast:
            raise Exception('Failed to parse %s' % idl_filename)
        idl_file_basename, _ = os.path.splitext(os.path.basename(idl_filename))
        definitions = IdlDefinitions(ast)

        validate_blink_idl_definitions(idl_filename, idl_file_basename,
                                       definitions)

        # Validate extended attributes
        if not self.extended_attribute_validator:
            return definitions

        try:
            self.extended_attribute_validator.validate_extended_attributes(
                definitions)
        except IDLInvalidExtendedAttributeError as error:
            raise IDLInvalidExtendedAttributeError("""
IDL ATTRIBUTE ERROR in file:
%s:
    %s
If you want to add a new IDL extended attribute, please add it to:
    %s
and add an explanation to the Blink IDL documentation at:
    http://www.chromium.org/blink/webidl/blink-idl-extended-attributes
    """ % (idl_filename, str(error), EXTENDED_ATTRIBUTES_RELATIVE_PATH))

        return definitions

def find_all_files_by_suffix(target_dir:str, suffix:str):
    result = []
    for path, _, files in os.walk(target_dir):
        for file in files:
            if file.endswith(suffix):
                result.append(os.path.join(path, file))
    return result

if __name__ == '__main__':
    reader = IdlReader(outputdir="./out")
    idl_files_dir = './src/third_party/blink/renderer/modules'
    for file in find_all_files_by_suffix(idl_files_dir, '.idl'):
        result = reader.read_idl_file(file)
        
        print('-'*196)
        print(f"  --*-- {file} --*--")
        if result.dictionaries:
            print("  [Dictionary]")
            for key in result.dictionaries.keys():
                item = result.dictionaries[key]
                print(f"    [{key}]")
                for member in item.members:
                    print(f"      {member.idl_type} {member.name}")
        
        if result.interfaces:
            print("  [Interface]")
            for key in result.interfaces.keys():
                interface = result.interfaces[key]
                print(f"    [{key}]")
                print(f"      [Attr]")
                for attr in interface.attributes:
                    print(f"        {attr.name}")
                print(f"      [Method]")
                for method in interface.operations:
                    args = []
                    for arg in method.arguments:
                        args.append(arg.name)
                    args_pass = ', '.join(args)
                    print(f"        {method.idl_type} {method.name}({args_pass})")