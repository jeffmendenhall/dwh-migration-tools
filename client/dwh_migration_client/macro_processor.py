# Copyright 2022 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""A processor to handle macros in the query files during the pre-processing and
post-processing stages of a Batch Sql Translation job.
"""

import logging
import os
import shutil
from os.path import abspath, dirname, isfile, join
from pprint import pformat
from typing import Dict

import yaml
from marshmallow import Schema, ValidationError, fields
from yaml.loader import SafeLoader

from dwh_migration_client.macro_expander import MacroExpanderRouter


class MacroProcessor:
    """A processor to handle macros in the query files during the pre-processing and
    post-processing stages of a Batch Sql Translation job.
    """

    def __init__(self, expander: MacroExpanderRouter) -> None:
        self.expander = expander

    def preprocess(self, input_dir: str, tmp_dir: str) -> None:
        """The pre-upload entry point of a MacroProcessor.

        This method expands customer-specific macros and substitutions in the
        source-language SQL, to make it valid for the compiler.

        Args:
            input_dir: path to the input directory.
            tmp_dir: path to a tmp directory that stores the files after preprocessing.
        """
        self._process(abspath(input_dir), abspath(tmp_dir), revert_expansion=False)

    def postprocess(self, tmp_dir: str, output_dir: str) -> None:
        """The post-download entry point of a MacroProcessor

        This method re-inserts macros into the generated target-language SQL, if
        required.

        Args:
            tmp_dir: path to the tmp directory that stores the outputs of a Translation
                job. These files are inputs to the postprocessing stage.
            output_dir: path to the directory that stores the final outputs after
                preprocessing.
        """
        self._process(abspath(tmp_dir), abspath(output_dir), revert_expansion=True)

    def is_ignored(self, path: str, name: str) -> bool:
        """Returns true if a file is ignored.

        Ignored files are not transpiled or copied to the output directory.
        """
        if not isfile(path):
            return True
        if name.startswith("."):
            return True
        return False

    def is_processable(self, path: str, name: str) -> bool:
        """Returns true if a file is preprocessable.

        Preprocessable files are subject to macro expansion and (optionally)
        unexpansion. Non-preprocessable files are transpiled verbatim. To ignore a file
        entirely, modify is_ignored.
        """
        if self.is_ignored(path, name):
            return False
        if name.lower().endswith((".zip", ".json", ".csv")):
            return False
        return True

    def _process(
        self, input_dir: str, output_dir: str, revert_expansion: bool = False
    ) -> None:
        """Replaces or restores macros for every file in the input folder and save
        outputs in a new folder.

        Macro replacement doesn't apply for files which are ignored, or not processable.
        Note that this method is called for varying combinations of input and output
        directories at different points in the process.

        Args:
            input_dir: absolute path to the input directory.
            output_dir: absolute path to the output directory.
            revert_expansion: whether to revert the macro substitution.
        """
        for root, _, files in os.walk(input_dir):
            for name in files:
                sub_dir = root[len(input_dir) + 1 :]
                input_path = join(input_dir, sub_dir, name)
                output_path = join(output_dir, sub_dir, name)
                if self.is_ignored(input_path, name):
                    continue
                os.makedirs(dirname(output_path), exist_ok=True)
                if not self.is_processable(input_path, name):
                    shutil.copy(input_path, output_path)
                    continue
                # The user may implement entirely different logic for macro expansion
                # vs. unexpansion, especially if they are migrating between systems,
                # so we use a boolean flag to separate the paths again here.
                if not revert_expansion:
                    self.preprocess_file(input_path, output_path, input_dir)
                else:
                    self.postprocess_file(input_path, output_path, output_dir)

    def preprocess_file(self, input_path: str, tmp_path: str, input_dir: str) -> None:
        """Replaces macros for the input file and save the output file in a tmp path.

        Args:
            input_path: absolute path to the input file.
            tmp_path: absolute path to the output tmp file.
            input_dir: absolute path to the input directory. The input file can be in a
                subdirectory in the input_dir.
        """
        logging.info("Preprocessing %s", input_path)
        with open(input_path, encoding="utf-8") as input_fh:
            text = input_fh.read()
        text = self.preprocess_text(text, input_path[len(input_dir) + 1 :])
        with open(tmp_path, "w", encoding="utf-8") as tmp_fh:
            tmp_fh.write(text)

    def preprocess_text(self, text: str, relative_input_path: str) -> str:
        """Preprocesses the given text, after conversion to the target dialect.

        Args:
            text: input text for processing.
            relative_input_path: relative path of the input file in the input_dir, e.g.,
                subdir/subdir_2/sample.sql.
        """
        return self.expander.expand(relative_input_path, text)

    def postprocess_file(
        self, tmp_path: str, output_path: str, output_dir: str
    ) -> None:
        """Postprocesses the given file, after conversion to the target dialect.

        The user may replace this method with any locally-specified implementation.
        If only simple textual replacement is required, it may be easier to modify
        postprocess_text.

        Not all users will want postprocessing, and some may just copy the file.

        Args:
            tmp_path: absolute path to the tmp file.
            output_path: absolute path to the output file after postprocessing.
            output_dir: absolute path to the output directory. The output file can be in
                a subdirectory in the output_dir.
        """
        logging.info("Postprocessing into %s", output_path)
        with open(tmp_path, encoding="utf-8") as tmp_fh:
            text = tmp_fh.read()
        text = self.postprocess_text(text, output_path[len(output_dir) + 1 :])
        with open(output_path, "w", encoding="utf-8") as output_fh:
            output_fh.write(text)

    def postprocess_text(self, text: str, relative_output_path: str) -> str:
        """Postprocesses the given text, after conversion to the target dialect.

        The user may replace this method with any locally-specified implementation.
        If access to the file is required, modify postprocess_file instead, and
        (optionally) delete this method.

        Not all users will want postprocessing, and some may just return text.

        Args:
            text: input text for processing.
            relative_output_path: relative path of the output file in the output_dir,
                e.g., subdir/subdir_2/sample.sql.
        """
        return self.expander.unexpand(relative_output_path, text)


class MacrosSchema(Schema):
    macros = fields.Dict(
        keys=fields.String(),
        values=fields.Dict(keys=fields.String(), values=fields.String(), required=True),
        required=True,
    )


def parse_macros_config_file(yaml_file_path: str) -> Dict[str, Dict[str, str]]:
    """Parses the macros mapping yaml file.

    Return:
        macros_replacement_maps: mapping from macros to the replacement string for
            each file.  {file_name: {macro: replacement}}. File name supports
            wildcard, e.g., with "*.sql", the method will apply the macro map to all
            the files with extension of ".sql".
    """
    logging.info("Parsing macros file: %s.", yaml_file_path)
    with open(yaml_file_path, encoding="utf-8") as file:
        data = yaml.load(file, Loader=SafeLoader)
    try:
        validated_data: Dict[str, Dict[str, Dict[str, str]]] = MacrosSchema().load(data)
    except ValidationError as error:
        logging.error("Invalid macros file: %s: %s.", yaml_file_path, error)
        raise
    logging.info(
        "Finished parsing macros file: %s:\n%s.",
        yaml_file_path,
        pformat(validated_data),
    )
    return validated_data["macros"]
