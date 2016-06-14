from __future__ import absolute_import, division, print_function

import logging
import multiprocessing
import os
import sys
import warnings
from collections import defaultdict
from os.path import join, normpath
import subprocess

import conda.config as cc
from conda.compat import text_type, PY3

from conda_build import external
from conda_build import source
from conda_build.config import config
from conda_build.features import feature_list
from conda_build.scripts import prepend_bin_path


def get_perl_ver():
    return str(config.CONDA_PERL)


def get_lua_ver():
    return str(config.CONDA_LUA)


def get_py_ver():
    return '.'.join(str(config.CONDA_PY))


def get_npy_ver():
    if config.CONDA_NPY:
        # Convert int -> string, e.g.
        #   17 -> '1.7'
        #   110 -> '1.10'
        conda_npy = str(config.CONDA_NPY)
        return conda_npy[0] + '.' + conda_npy[1:]
    return ''


def get_stdlib_dir():
    return join(config.build_prefix, 'Lib' if sys.platform == 'win32' else
                'lib/python%s' % get_py_ver())


def get_lua_include_dir():
    return join(config.build_prefix, "include")


def get_sp_dir():
    return join(get_stdlib_dir(), 'site-packages')


def verify_git_repo(git_dir, git_url, expected_rev='HEAD'):
    env = os.environ.copy()

    if not expected_rev:
        return False

    env['GIT_DIR'] = git_dir
    try:
        # Verify current commit matches expected commit
        current_commit = subprocess.check_output(["git", "log", "-n1", "--format=%H"],
                                      env=env, stderr=subprocess.STDOUT)
        current_commit = current_commit.decode('utf-8')
        expected_tag_commit = subprocess.check_output(["git", "log", "-n1", "--format=%H",
                                            expected_rev],
                                           env=env, stderr=subprocess.STDOUT)
        expected_tag_commit = expected_tag_commit.decode('utf-8')

        if current_commit != expected_tag_commit:
            return False

        # Verify correct remote url. Need to find the git cache directory,
        # and check the remote from there.
        cache_details = subprocess.check_output(["git", "remote", "-v"], env=env,
                                     stderr=subprocess.STDOUT)
        cache_details = cache_details.decode('utf-8')
        cache_dir = cache_details.split('\n')[0].split()[1]

        if not isinstance(cache_dir, str):
            # On Windows, subprocess env can't handle unicode.
            cache_dir = cache_dir.encode(sys.getfilesystemencoding() or 'utf-8')

        remote_details = subprocess.check_output(["git", "--git-dir", cache_dir, "remote", "-v"],
                                                 env=env, stderr=subprocess.STDOUT)
        remote_details = remote_details.decode('utf-8')
        remote_url = remote_details.split('\n')[0].split()[1]

        # on windows, remote URL comes back to us as cygwin or msys format.  Python doesn't
        # know how to normalize it.  Need to convert it to a windows path.
        if sys.platform == 'win32' and remote_url.startswith('/'):
            cmd = "cygpath -w remote_url"
            if PY3:
                remote_url = subprocess.getoutput(cmd)
            else:
                remote_url = subprocess.check_output(cmd.split()).rstrip().rstrip("\\")

        if os.path.exists(remote_url):
            # Local filepaths are allowed, but make sure we normalize them
            remote_url = normpath(remote_url)

        # If the current source directory in conda-bld/work doesn't match the user's
        # metadata git_url or git_rev, then we aren't looking at the right source.
        if remote_url.lower() != git_url.lower():
            logging.debug("\nremote does not match git_url\n")
            logging.debug("Remote: " + remote_url.lower() + "\n")
            logging.debug("git_url: " + git_url.lower() + "\n")
            return False
    except subprocess.CalledProcessError as error:
        logging.warn("Error obtaining git information.  Error was: ")
        logging.warn(error)
        return False
    return True


def get_git_info(repo):
    """
    Given a repo to a git repo, return a dictionary of:
      GIT_DESCRIBE_TAG
      GIT_DESCRIBE_NUMBER
      GIT_DESCRIBE_HASH
      GIT_FULL_HASH
      GIT_BUILD_STR
    from the output of git describe.
    :return:
    """
    d = {}

    # grab information from describe
    env = os.environ.copy()
    env['GIT_DIR'] = repo
    keys = ["GIT_DESCRIBE_TAG", "GIT_DESCRIBE_NUMBER", "GIT_DESCRIBE_HASH"]

    process = subprocess.Popen(["git", "describe", "--tags", "--long", "HEAD"],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    env=env)
    output = process.communicate()[0].strip()
    output = output.decode('utf-8')

    parts = output.rsplit('-', 2)
    if len(parts) == 3:
        d.update(dict(zip(keys, parts)))

    # get the _full_ hash of the current HEAD
    process = subprocess.Popen(["git", "rev-parse", "HEAD"],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    output = process.communicate()[0].strip()
    output = output.decode('utf-8')

    d['GIT_FULL_HASH'] = output
    # set up the build string
    if "GIT_DESCRIBE_NUMBER" in d and "GIT_DESCRIBE_HASH" in d:
        d['GIT_BUILD_STR'] = '{}_{}'.format(d["GIT_DESCRIBE_NUMBER"],
                                            d["GIT_DESCRIBE_HASH"])

    return d


def get_dict(m=None, prefix=None, dirty=False):
    if not prefix:
        prefix = config.build_prefix

    # conda-build specific vars
    d = conda_build_vars(prefix, dirty)

    # languages
    d.update(python_vars())
    d.update(perl_vars())
    d.update(lua_vars())

    if m:
        d.update(meta_vars(m))

    # system
    d.update(system_vars(d, prefix))

    # features
    d.update({feat.upper(): str(int(value)) for feat, value in
              feature_list})

    return d


def conda_build_vars(prefix, dirty):
    return {
        'CONDA_BUILD': '1',
        'PYTHONNOUSERSITE': '1',
        'CONDA_DEFAULT_ENV': config.build_prefix,
        'ARCH': str(cc.bits),
        'PREFIX': prefix,
        'SYS_PREFIX': sys.prefix,
        'SYS_PYTHON': sys.executable,
        'SUBDIR': cc.subdir,
        'SRC_DIR': source.get_dir(),
        'HTTPS_PROXY': os.getenv('HTTPS_PROXY', ''),
        'HTTP_PROXY': os.getenv('HTTP_PROXY', ''),
        'DIRTY': '1' if dirty else '',
    }


def python_vars():
    vars = {
        'PYTHON': config.build_python,
        'PY3K': str(config.PY3K),
        'STDLIB_DIR': get_stdlib_dir(),
        'SP_DIR': get_sp_dir(),
        'PY_VER': get_py_ver(),
        'CONDA_PY': str(config.CONDA_PY),
    }
    # Only define these variables if '--numpy=X.Y' was provided,
    # otherwise any attempt to use them should be an error.
    if get_npy_ver():
        vars['NPY_VER'] = get_npy_ver()
        vars['CONDA_NPY'] = str(config.CONDA_NPY)
    return vars


def perl_vars():
    return {
        'PERL_VER': get_perl_ver(),
    }


def lua_vars():
    lua = config.build_lua
    if lua:
        return {
            'LUA': lua,
            'LUA_INCLUDE_DIR': get_lua_include_dir(),
            'LUA_VER': get_lua_ver(),
        }
    else:
        return {}


def meta_vars(meta):
    d = {}
    for var_name in meta.get_value('build/script_env', []):
        value = os.getenv(var_name)
        if value is None:
            warnings.warn(
                "The environment variable '%s' is undefined." % var_name,
                UserWarning
            )
        else:
            d[var_name] = value

    git_dir = join(source.get_dir(), '.git')
    if not isinstance(git_dir, str):
        # On Windows, subprocess env can't handle unicode.
        git_dir = git_dir.encode(sys.getfilesystemencoding() or 'utf-8')

    if external.find_executable('git') and os.path.exists(git_dir):
        git_url = meta.get_value('source/git_url')

        if os.path.exists(git_url):
            # If git_url is a relative path instead of a url, convert it to an abspath
            git_url = normpath(join(meta.path, git_url))
            if sys.platform == 'win32':
                cmd = "cygpath -w remote_url"
                if PY3:
                    git_url = subprocess.getoutput(cmd)
                else:
                    git_url = subprocess.check_output(cmd.split()).rstrip().rstrip("\\")

        _x = False

        if git_url:
            _x = verify_git_repo(git_dir,
                                 git_url,
                                 meta.get_value('source/git_rev', 'HEAD'))

        if _x or meta.get_value('source/path'):
            d.update(get_git_info(git_dir))

    d['PKG_NAME'] = meta.name()
    d['PKG_VERSION'] = meta.version()
    d['PKG_BUILDNUM'] = str(meta.build_number())
    d['PKG_BUILD_STRING'] = str(meta.build_id())
    d['RECIPE_DIR'] = meta.path
    return d


def get_cpu_count():
    if sys.platform == "darwin":
        # multiprocessing.cpu_count() is not reliable on OSX
        # See issue #645 on github.com/conda/conda-build
        out, err = subprocess.Popen('sysctl -n hw.logicalcpu', shell=True,
                         stdout=subprocess.PIPE).communicate()
        return out.decode('utf-8').strip()
    else:
        try:
            return str(multiprocessing.cpu_count())
        except NotImplementedError:
            return "1"


def windows_vars(prefix):
    library_prefix = join(prefix, 'Library')
    drive, tail = prefix.split(':')
    return {
        'SCRIPTS': join(prefix, 'Scripts'),
        'LIBRARY_PREFIX': library_prefix,
        'LIBRARY_BIN': join(library_prefix, 'bin'),
        'LIBRARY_INC': join(library_prefix, 'include'),
        'LIBRARY_LIB': join(library_prefix, 'lib'),
        'R': join(prefix, 'Scripts', 'R.exe'),
        'CYGWIN_PREFIX': ''.join(('/cygdrive/', drive.lower(), tail.replace('\\', '/')))
    }


def unix_vars(prefix):
    return {
        'HOME': os.getenv('HOME', 'UNKNOWN'),
        'PKG_CONFIG_PATH': join(prefix, 'lib', 'pkgconfig'),
        'CMAKE_GENERATOR': 'Unix Makefiles',
        'R': join(prefix, 'bin', 'R'),
    }


def osx_vars(compiler_vars):
    OSX_ARCH = 'i386' if cc.bits == 32 else 'x86_64'
    compiler_vars['CFLAGS'] += ' -arch {0}'.format(OSX_ARCH)
    compiler_vars['CXXFLAGS'] += ' -arch {0}'.format(OSX_ARCH)
    compiler_vars['LDFLAGS'] += ' -arch {0}'.format(OSX_ARCH)
    # 10.7 install_name_tool -delete_rpath causes broken dylibs, I will revisit this ASAP.
    # rpath = ' -Wl,-rpath,%(PREFIX)s/lib' % d # SIP workaround, DYLD_* no longer works.
    # d['LDFLAGS'] = ldflags + rpath + ' -arch %(OSX_ARCH)s' % d
    return {
        'OSX_ARCH': OSX_ARCH,
        'MACOSX_DEPLOYMENT_TARGET': '10.6',
    }


def linux_vars(compiler_vars, prefix):
    compiler_vars['LD_RUN_PATH'] = prefix + '/lib'
    if cc.bits == 32:
        compiler_vars['CFLAGS'] += ' -m32'
        compiler_vars['CXXFLAGS'] += ' -m32'
    return {}


def system_vars(env_dict, prefix):
    d = dict()
    compiler_vars = defaultdict(text_type)

    if 'MAKEFLAGS' in os.environ:
        d['MAKEFLAGS'] = os.environ['MAKEFLAGS']

    d['CPU_COUNT'] = get_cpu_count()
    if "LANG" in os.environ:
        d['LANG'] = os.environ['LANG']
    d['PATH'] = os.environ['PATH']
    d = prepend_bin_path(d, prefix)

    if sys.platform == 'win32':
        d.update(windows_vars(prefix))
    else:
        d.update(unix_vars(prefix))

    if sys.platform == 'darwin':
        d.update(osx_vars(compiler_vars))
    elif sys.platform.startswith('linux'):
        d.update(linux_vars(compiler_vars, prefix))

    # make sure compiler_vars get appended to anything already set, including build/script_env
    for key in compiler_vars:
        if key in env_dict:
            compiler_vars[key] += env_dict[key]
    d.update(compiler_vars)

    return d


# http://code.activestate.com/recipes/576644-diff-two-dictionaries/#c9
def _dict_diff(d1, d2):
    """Shows entries that have changed or been added in d2 relative to d1"""
    both = set(d1.keys()) & set(d2.keys())
    diff = {k: d2[k] for k in both if d1[k] != d2[k]}
    diff.update({k: d2[k] for k in set(d2.keys()) - both})
    return diff


def _set_environ_from_subprocess_values(vars):
    """
    Vars is an unprocessed string of envrionment variable output, such as from ```set```
    on Windows, or ```env``` elsewhere
    """
    start_environ = os.environ
    vars = vars.split("\n")
    modified_environ = {var.split("=")[0].strip(): var.split("=")[1].strip()
                        for var in vars if "=" in var}

    # the diff is the only part we'll set for the actual build environment - mind you,
    #   the current process is not the actual build environment.  That is always a native
    #   shell subprocess.
    diff = _dict_diff(start_environ, modified_environ)

    # modify the current process with the activated/deactivated values
    for key, value in modified_environ.items():
        os.environ[key] = value

    return diff


def activate_or_deactivate_env(action, env_name_or_path=""):
    """
    Strategy is to open a subprocess, run activate, record the variables,
    then apply them in our process

    action should be "activate" or "deactivate"
    env_name_or_path should only be provided for activation.
    """
    if sys.platform == "win32":
        cmd = '"{0}\\Scripts\\{1}.bat" "{2}" && set'.format(sys.prefix,
                                                            action,
                                                            env_name_or_path)
        if PY3:
            # this method simplifies dealing with str vs bytestring on Py3.
            vars = subprocess.getoutput(cmd).replace("\r\n", "\n")
        else:
            vars = subprocess.check_output(cmd.split(),
                                           env=os.environ).replace("\r\n", "\n")
    else:
        cmd = "source {0}/bin/{1} {2} && env".format(sys.prefix, action, env_name_or_path)
        vars = subprocess.check_output(["bash", "-c", cmd], env=os.environ)
    if PY3 and hasattr(vars, "decode"):
        vars = vars.decode("UTF-8")
    return _set_environ_from_subprocess_values(vars)


def activate_env(env_name_or_path):
    return activate_or_deactivate_env("activate", env_name_or_path)


def deactivate_env():
    return activate_or_deactivate_env("deactivate")


if __name__ == '__main__':
    e = get_dict()
    for k in sorted(e):
        assert isinstance(e[k], str), k
        print('%s=%s' % (k, e[k]))
