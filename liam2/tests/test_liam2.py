# -*- coding: utf-8 -*-


from __future__ import print_function

import os
import pkg_resources

import liam2
from liam2.simulation import Simulation


def run_simulation(yaml_file, input_dir, output_dir):
    # try:
        simulation = Simulation.from_yaml(
            yaml_file,
            input_dir = input_dir,
            output_dir = output_dir,
            )
        simulation.run(False)
    # except Exception:
    #     print('{} failed'.format(yaml_file))
    #     raise


def test_liam2_examples_files():
    liam2_examples_directory = os.path.join(
        pkg_resources.get_distribution('liam2').location,
        'liam2',
        'tests',
        'examples'
        )
    excluded_files = [
        'demo_import.yml',  # import file not need to test
        'demo02.yml',  # TODO: pb with figures
        ]
    yaml_files = [os.path.join(liam2_examples_directory, _file) for _file in os.listdir(liam2_examples_directory)
        if os.path.isfile(os.path.join(liam2_examples_directory, _file))
        and _file.endswith('.yml')
        and _file not in excluded_files]

    input_dir = os.path.join(liam2_examples_directory)
    output_dir = os.path.join(
        pkg_resources.get_distribution('liam2').location,
        'liam2',
        'tests',
        'output',
        )
    for yaml_file in yaml_files:
        yield run_simulation, yaml_file, input_dir, output_dir


def test_liam2_functionnal_files():
    liam2_functional_directory = os.path.join(
        pkg_resources.get_distribution('liam2').location,
        'liam2',
        'tests',
        'functional'
        )
    input_dir = os.path.join(liam2_functional_directory)
    output_dir = os.path.join(
        pkg_resources.get_distribution('liam2').location,
        'tests',
        'output'
        )
    yaml_files = ['simulation.yml']  # TODO 'import.yml' doesn't work
    for yaml_file in yaml_files:
        yaml_path = os.path.join(liam2_functional_directory, yaml_file)
        assert os.path.exists(yaml_path), '{} does not exists'.format(yaml_path)
        yield run_simulation, yaml_path, input_dir, output_dir
