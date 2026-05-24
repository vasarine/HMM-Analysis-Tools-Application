from celery import shared_task
from django.core.management import call_command
import logging

logger = logging.getLogger(__name__)


@shared_task(name='cleanup_old_projects_task')
def cleanup_old_projects_task():
    logger.info("Starting automatic cleanup of old projects")

    try:
        call_command('cleanup_old_projects')
        logger.info("Old projects cleanup completed successfully")
        return {'status': 'success', 'message': 'Cleanup completed'}
    except Exception as e:
        logger.error(f"Error cleaning up old projects: {str(e)}")
        raise
