#!/usr/bin/env python3

try:
    from setuptools.core import setup
except ImportError:
    from distutils.core import setup
from pip.req import parse_requirements
from pip.download import PipSession
import os

def get_file_path(name):
    return os.path.abspath(os.path.join(
        os.path.dirname(__file__),
        name))

with open(get_file_path('version.py')) as f:
    exec(f.read())

# read requirements from requirements.txt
requirements = parse_requirements(get_file_path('requirements.txt'),
                                  session=PipSession())
requirements = [str(r.req) for r in requirements]

setup(
    name = 'pybtracker',
    py_modules = ['version'],
    packages = ['pybtracker'],
    install_requires = requirements,
    version = __version__,
    description = 'Simple asyncio-based UDP BitTorrent tracker, '
                  'with a simple client.',
    author = 'Mostafa Razavi',
    license = 'MIT',
    author_email = 'mostafa@sepent.com',
    url = 'https://github.com/elektito/pybtracker',
    download_url = 'https://github.com/elektito/pybtracker/tarball/' + __version__,
    keywords = ['bittorrent', 'torrent', 'tracker', 'asyncio', 'udp'],
    classifiers = [
        'Programming Language :: Python :: 3'
    ],
    entry_points = {
        'console_scripts': [
            'pybtracker=pybtracker.server:main',
            'pybtracker-client=pybtracker.client:main',
        ],
    },
)
