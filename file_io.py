"""Filesystem boundaries independent of Windows system long-path policy.

Keep ordinary paths in projects/UI; only OS/library IO receives extended paths.
"""
from pathlib import Path
import ntpath
import os
import uuid


def windows_path(path):
    """Pure conversion, also testable without an available UNC server."""
    value=ntpath.abspath(os.fspath(path))
    if value.startswith('\\\\?\\'):return value
    if value.startswith('\\\\'):return '\\\\?\\UNC\\'+value[2:]
    return '\\\\?\\'+value


def io_path(path):
    return Path(windows_path(path)) if os.name=='nt' else Path(path)


def temporary_path(destination):
    # Do not grow the destination basename; share its filesystem for atomic rename.
    return io_path(destination).with_name('.fi-'+uuid.uuid4().hex+'.part')
