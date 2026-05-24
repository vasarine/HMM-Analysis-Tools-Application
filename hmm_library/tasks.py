from celery import shared_task
from django.utils import timezone
import logging

from .models import ExternalHMMModel, HMMDownloadLog
from .services import HMMCacheManager

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_kwargs={'max_retries': 3, 'countdown': 5},
    soft_time_limit=120,
    time_limit=180
)
def download_hmm_async(self, source: str, external_id: str):
    logger.info(f"Starting async HMM download: {source}:{external_id}")

    try:
        file_path = HMMCacheManager.get_or_download(source, external_id)

        if file_path:
            logger.info(f"Successfully obtained HMM for {source}:{external_id}")
            return {
                'success': True,
                'file_path': file_path,
                'source': source,
                'external_id': external_id,
                'message': f'Successfully downloaded {source}:{external_id}'
            }
        else:
            logger.error(f"Failed to obtain HMM for {source}:{external_id}")
            return {
                'success': False,
                'file_path': None,
                'source': source,
                'external_id': external_id,
                'message': f'Failed to download {source}:{external_id}'
            }

    except Exception as e:
        logger.error(f"Error downloading {source}:{external_id}: {e}")
        return {
            'success': False,
            'file_path': None,
            'source': source,
            'external_id': external_id,
            'message': f'Error: {str(e)}'
        }


@shared_task
def cleanup_expired_cache():
    logger.info("Starting HMM cache cleanup...")

    expired_count = HMMCacheManager.cleanup_expired()
    old_count = HMMCacheManager.cleanup_old(days=180)

    total_cleaned = expired_count + old_count

    logger.info(f"Cache cleanup completed: {expired_count} expired, {old_count} old")

    return {
        'expired_count': expired_count,
        'old_count': old_count,
        'total_cleaned': total_cleaned,
        'timestamp': timezone.now().isoformat()
    }


@shared_task
def preload_popular_hmms(pfam_ids: list):
    logger.info(f"Pre-loading {len(pfam_ids)} HMM models...")

    results = {
        'success': [],
        'failed': [],
        'already_cached': []
    }

    for pfam_id in pfam_ids:
        try:
            existing = ExternalHMMModel.objects.filter(
                source='pfam',
                external_id=pfam_id.upper()
            ).first()

            if existing and not existing.is_expired():
                results['already_cached'].append(pfam_id)
                logger.info(f"{pfam_id} already cached")
                continue

            file_path = HMMCacheManager.get_or_download('pfam', pfam_id)

            if file_path:
                results['success'].append(pfam_id)
                logger.info(f"Successfully pre-loaded {pfam_id}")
            else:
                results['failed'].append(pfam_id)
                logger.error(f"Failed to pre-load {pfam_id}")

        except Exception as e:
            results['failed'].append(pfam_id)
            logger.error(f"Error pre-loading {pfam_id}: {e}")

    logger.info(f"Pre-load completed: {len(results['success'])} success, "
                f"{len(results['failed'])} failed, "
                f"{len(results['already_cached'])} already cached")

    return results


@shared_task
def update_cache_metadata(source: str, external_id: str):
    try:
        model = ExternalHMMModel.objects.get(
            source=source,
            external_id=external_id.upper()
        )

        if source == 'pfam':
            from .services import PfamAPIClient
            metadata = PfamAPIClient.get_entry_metadata(external_id)
        elif source == 'interpro':
            from .services import InterProAPIClient
            metadata = InterProAPIClient.get_entry_metadata(external_id)
        else:
            return {'success': False, 'message': 'Invalid source'}

        if metadata:
            model.name = metadata.get('name', model.name)
            model.description = metadata.get('description', model.description)
            model.version = metadata.get('version', model.version)
            model.save()

            logger.info(f"Updated metadata for {source}:{external_id}")
            return {'success': True, 'message': 'Metadata updated'}
        else:
            return {'success': False, 'message': 'Failed to fetch metadata'}

    except ExternalHMMModel.DoesNotExist:
        return {'success': False, 'message': 'Model not found in cache'}
    except Exception as e:
        logger.error(f"Error updating metadata for {source}:{external_id}: {e}")
        return {'success': False, 'message': str(e)}
