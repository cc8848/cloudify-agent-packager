import logger
import logging
import yaml
import json
import platform
import shutil
import os
import sys

import utils
import codes

from jingen.jingen import Jingen

DEFAULT_CONFIG_FILE = 'config.yaml'
DEFAULT_OUTPUT_TAR_PATH = '{0}-{1}-agent.tar.gz'
DEFAULT_VENV_PATH = 'cloudify/{0}-{1}-agent/env'

INCLUDES_FILE = 'included_plugins.py'
TEMPLATE_FILE = 'included_plugins.py.j2'
TEMPLATE_DIR = 'resources'

EXTERNAL_MODULES = [
    'celery==3.0.24'
]

CORE_MODULES_LIST = [
    'cloudify_rest_client',
    'cloudify_plugins_common',
]

CORE_PLUGINS_LIST = [
    'cloudify_script_plugin',
    'cloudify_diamond_plugin',
    # 'cloudify_agent_installer_plugin',
    # 'cloudify_plugin_installer_plugin',
    # 'cloudify_windows_agent_installer_plugin',
    # 'cloudify_windows_plugin_installer_plugin',
]

MANDATORY_MODULES = [
    'cloudify_rest_client',
    'cloudify_plugins_common',
    # 'cloudify_agent_installer_plugin',
    # 'cloudify_plugin_installer_plugin',
    # 'cloudify_windows_agent_installer_plugin',
    # 'cloudify_windows_plugin_installer_plugin',
]

DEFAULT_CLOUDIFY_AGENT_URL = 'https://github.com/nir0s/cloudify-agent/archive/{0}.tar.gz'  # NOQA

lgr = logger.init()
verbose_output = False


def set_global_verbosity_level(is_verbose_output=False):
    """sets the global verbosity level for console and the lgr logger.

    :param bool is_verbose_output: should be output be verbose
    """
    global verbose_output
    # TODO: (IMPRV) only raise exceptions in verbose mode
    verbose_output = is_verbose_output
    if verbose_output:
        lgr.setLevel(logging.DEBUG)
    else:
        lgr.setLevel(logging.INFO)


def _import_config(config_file=DEFAULT_CONFIG_FILE):
    """returns a configuration object

    :param string config_file: path to config file
    """
    # get config file path
    lgr.debug('Config file is: {0}'.format(config_file))
    # append to path for importing
    try:
        lgr.debug('Importing config...')
        with open(config_file, 'r') as c:
            return yaml.safe_load(c.read())
    except IOError as ex:
        lgr.error(str(ex))
        lgr.error('Cannot access config file')
        sys.exit(codes.mapping['could_not_access_config_file'])
    except (yaml.parser.ParserError, yaml.scanner.ScannerError) as ex:
        lgr.error(str(ex))
        lgr.error('Invalid yaml file')
        sys.exit(codes.mapping['invalid_yaml_file'])


def _set_defaults(modules):
    modules['core_modules'] = {}
    modules['core_plugins'] = {}
    modules['additional_modules'] = []
    modules['additional_plugins'] = {}
    modules['agent'] = ""
    return modules


def _merge_modules(modules, config):
    """merges the default modules with the modules from the config yaml

    :param dict modules: dict containing core and additional
    modules and the cloudify-agent module.
    :param dict config: dict containing the config.
    """
    modules['core_modules'].update(config.get('core_modules', {}))
    modules['core_plugins'].update(config.get('core_plugins', {}))

    additional_modules = config.get('additional_modules', [])
    for additional_module in additional_modules:
        modules['additional_modules'].append(additional_module)
    modules['additional_plugins'].update(config.get('additional_plugins', {}))

    if 'cloudify_agent_module' in config:
        modules['agent'] = config['cloudify_agent_module']
    elif 'cloudify_agent_version' in config:
        modules['agent'] = DEFAULT_CLOUDIFY_AGENT_URL.format(
            config['cloudify_agent_version'])
    else:
        lgr.error('Either `cloudify_agent_module` or `cloudify_agent_version` '
                  'must be specified in the yaml configuration file.')
        sys.exit(codes.mapping['missing_cloudify_agent_config'])
    return modules


def _validate(modules, venv):
    """validates that all requested modules are actually installed
    within the virtualenv

    :param dict modules: dict containing core and additional
    modules and the cloudify-agent module.
    :param string venv: path of virtualenv to install in.
    """

    failed = []

    lgr.info('Validating installation...')
    modules = modules['plugins'] + modules['modules']
    for module_name in modules:
        # module_name = get_module_name(module)
        lgr.info('Validating that {0} is installed.'.format(module_name))
        if not utils.check_installed(module_name, venv):
            lgr.error('It appears that {0} does not exist in {1}'.format(
                module_name, venv))
            failed.append(module_name)

    if failed:
        lgr.error('Validation failed. some of the requested modules were not '
                  'installed.')
        sys.exit(codes.mapping['installation_validation_failed'])


class ModuleInstaller():

    def __init__(self, modules, venv, final_set):
        self.venv = venv
        self.modules = modules
        self.final_set = final_set

    def install_modules(self, modules):
        for module in modules:
            lgr.info('Installing module {0}'.format(module))
            utils.install_module(module, self.venv)

    def install_core_modules(self):
        core = self.modules['core_modules']
        # we must run through the CORE_MODULES_LIST so that dependencies are
        # installed in order
        for module in CORE_MODULES_LIST:
            module_name = get_module_name(module)
            if core.get(module):
                lgr.info('Installing module {0} from {1}.'.format(
                    module_name, core[module]))
                utils.install_module(core[module], self.venv)
                self.final_set['modules'].append(module_name)
            elif not core.get(module) and module in MANDATORY_MODULES:
                lgr.info('Module {0} will be installed as a part of '
                         'cloudify-agent (This is a mandatory module).'.format(
                             module_name))
            elif not core.get(module):
                lgr.info('Module {0} will be installed as a part of '
                         'cloudify-agent (if applicable).'.format(module_name))

    def install_core_plugins(self):
        core = self.modules['core_plugins']

        for module in CORE_PLUGINS_LIST:
            module_name = get_module_name(module)
            if core.get(module) and core[module] == 'exclude':
                lgr.info('Module {0} is excluded. '
                         'it will not be a part of the agent.'.format(
                             module_name))
            elif core.get(module):
                lgr.info('Installing module {0} from {1}.'.format(
                    module_name, core[module]))
                utils.install_module(core[module], self.venv)
                self.final_set['plugins'].append(module_name)
            elif not core.get(module):
                lgr.info('Module {0} will be installed as a part of '
                         'cloudify-agent (if applicable).'.format(module_name))

    def install_additional_plugins(self):
        additional = self.modules['additional_plugins']

        for module, source in additional.items():
            module_name = get_module_name(module)
            lgr.info('Installing module {0} from {1}.'.format(
                module_name, source))
            utils.install_module(source, self.venv)
            self.final_set['plugins'].append(module_name)

    def install_agent(self):
        lgr.info('Installing cloudify-agent module from {0}'.format(
            self.modules['agent']))
        utils.install_module(self.modules['agent'], self.venv)
        self.final_set['modules'].append('cloudify-agent')


def _install(modules, venv, final_set):
    """installs all requested modules

    :param dict modules: dict containing core and additional
    modules and the cloudify-agent module.
    :param string venv: path of virtualenv to install in.
    """
    installer = ModuleInstaller(modules, venv, final_set)
    lgr.info('Installing external modules...')
    installer.install_modules(EXTERNAL_MODULES)
    lgr.info('Installing core modules...')
    installer.install_core_modules()
    lgr.info('Installing core plugins...')
    installer.install_core_plugins()
    lgr.info('Installing additional modules...')
    installer.install_modules(modules['additional_modules'])
    lgr.info('Installing additional plugins...')
    installer.install_additional_plugins()
    installer.install_agent()
    return installer.final_set


def get_module_name(module):
    return module.replace('_', '-')


def _update_includes_file(modules, venv):

    lgr.debug('generating includes file')

    site_packages_path = os.sep.join(
        [venv, 'lib', 'python' + sys.version[:3], 'site-packages'])
    output_file = os.path.join(
        site_packages_path, 'cloudify_agent', INCLUDES_FILE)
    lgr.info(site_packages_path)
    lgr.info(output_file)
    i = Jingen(
        template_file=TEMPLATE_FILE,
        vars_source=modules,
        output_file=output_file,
        template_dir=os.path.join(os.path.dirname(__file__), TEMPLATE_DIR),
        make_file=True
    )
    i.generate()


def create(config=None, config_file=None, force=False, dryrun=False,
           no_validate=False, verbose=True):
    """Creates an agent package (tar.gz)

    This will try to identify the distribution of the host you're running on.
    If it can't identify it for some reason, you'll have to supply a
    `distribution` config object in the config.yaml.

    A virtualenv will be created under `/DISTRIBUTION-agent/env` unless
    configured in the yaml under the `venv` property.
    The order of the modules' installation is as follows:

    cloudify-rest-service
    cloudify-plugins-common
    cloudify-script-plugin
    cloudify-diamond-plugin
    cloudify-agent-installer-plugin
    cloudify-plugin-installer-plugin
    cloudify-windows-agent-installer-plugin
    cloudify-windows-plugin-installer-plugin
    cloudify-agent
    any additional modules specified under `additional_modules` in the yaml.

    Once all modules are installed, a tar.gz file will be created. The
    `output_tar` config object can be specified to determine the path to the
    output file. If omitted, a default path will be given with the
    format `/DISTRIBUTION-agent.tar.gz`.
    """
    set_global_verbosity_level(verbose)
    final_set = {
        'modules': [],
        'plugins': []
    }

    if not config:
        config = _import_config(config_file) if config_file else \
            _import_config()
        config = {} if not config else config
    try:
        distro = config.get('distribution', platform.dist()[0])
        release = config.get('release', platform.dist()[2])
    except Exception as ex:
        lgr.error(
            'Distribution not found in configuration '
            'and could not be retrieved automatically. '
            'please specify the distribution in the yaml. '
            '({0})'.format(ex.message))
        sys.exit(codes.mapping['could_not_identify_distribution'])

    python = config.get('python_path', '/usr/bin/python')
    venv = config.get('venv', DEFAULT_VENV_PATH.format(distro, release))
    keep_venv = config.get('keep_venv', False)
    destination_tar = config.get('output_tar',
                                 DEFAULT_OUTPUT_TAR_PATH.format(
                                     distro, release))

    lgr.debug('Distibution is: {0}'.format(distro))
    lgr.debug('Distribution release is: {0}'.format(release))
    lgr.debug('Python path is: {0}'.format(python))
    lgr.debug('venv is: {0}'.format(venv))
    lgr.debug('Destination tarfile is: {0}'.format(destination_tar))

    # virtualenv
    if os.path.isdir(venv):
        if force:
            lgr.info('Removing previous virtualenv...')
            shutil.rmtree(venv)
        else:
            lgr.error('Virtualenv already exists at {0}. '
                      'You can use the -f flag or delete the '
                      'previous env.'.format(venv))
            sys.exit(codes.mapping['virtualenv_already_exists'])

    lgr.info('Creating virtualenv: {0}'.format(venv))
    utils.make_virtualenv(venv, python)

    # output file
    if os.path.isfile(destination_tar) and force:
        lgr.info('Removing previous agent package...')
        os.remove(destination_tar)
    if os.path.exists(destination_tar):
            lgr.error('Destination tar already exists: {0}'.format(
                destination_tar))
            sys.exit(codes.mapping['tar_already_exists'])

    # create modules dictionary
    lgr.debug('Retrieving modules to install...')
    modules = {}
    modules = _set_defaults(modules)
    modules = _merge_modules(modules, config)

    if dryrun:
        set_global_verbosity_level(True)
    lgr.debug('Modules to install: {0}'.format(json.dumps(
        modules, sort_keys=True, indent=4, separators=(',', ': '))))

    if dryrun:
        lgr.info('Dryrun complete')
        sys.exit(codes.mapping['dryrun_complete'])

    # install all requested modules
    final_set = _install(modules, venv, final_set)

    # uninstall excluded modules
    lgr.info('Uninstalling excluded plugins (if any)...')
    for module in CORE_PLUGINS_LIST:
        module_name = get_module_name(module)
        if modules['core_plugins'].get(module) == 'exclude' and \
                utils.check_installed(module_name, venv):
            lgr.info('uninstalling {0}'.format(module_name))
            utils.uninstall_module(module_name, venv)

    # validate that modules were installed
    if not no_validate:
        # _validate(modules, venv)
        _validate(final_set, venv)

    _update_includes_file(final_set, venv)

    # create agent tar
    lgr.info('Creating tar file: {0}'.format(destination_tar))
    utils.tar(venv, destination_tar)

    lgr.info('The following modules were installed in the agent:\n{0}'.format(
        utils.get_installed(venv)))

    # remove virtualenv dir
    if not keep_venv:
        lgr.info('Removing origin virtualenv')
        shutil.rmtree(venv)

    lgr.info('Process complete!')


class PackagerError(Exception):
    pass
