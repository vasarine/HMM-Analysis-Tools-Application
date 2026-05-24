"""
Small shared helpers, mostly for safely deleting a project's files
from storage.
"""

import logging
from typing import Iterable

logger = logging.getLogger(__name__)

def delete_filefield(ff) -> None:
    try:
        if ff and getattr(ff, "name", None):
            ff.delete(save=False)
    except Exception as e:
        logger.warning("Failed to delete file from storage: %s", e)

def delete_project_files(project, field_names: Iterable[str]) -> None:
    for fname in field_names:
        delete_filefield(getattr(project, fname, None))
    project.delete()
