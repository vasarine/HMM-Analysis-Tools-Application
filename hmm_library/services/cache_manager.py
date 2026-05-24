"""
Caches HMM profiles downloaded from Pfam/InterPro so the same id isn't
fetched twice, and logs the downloads.
"""

import hashlib
import logging
import os
from datetime import timedelta
from typing import Optional, Tuple

from django.core.files.base import ContentFile
from django.utils import timezone

from ..models import ExternalHMMModel, HMMDownloadLog
from .exceptions import RemoteUnavailable
from .interpro_client import InterProAPIClient
from .pfam_client import PfamAPIClient

logger = logging.getLogger(__name__)


class HMMCacheManager:
    DEFAULT_TTL_DAYS = 90

    @classmethod
    def get_or_download(cls, source: str, external_id: str) -> Optional[str]:
        if not cls._validate_id(source, external_id):
            logger.error(f"Invalid {source} ID: {external_id}")
            return None

        cached = cls._get_from_cache(source, external_id)
        if cached:
            logger.info(f"Cache HIT for {source}:{external_id}")
            return cached.hmm_file.path

        logger.info(f"Cache MISS for {source}:{external_id} - downloading...")
        downloaded = cls._download_and_cache(source, external_id)
        return downloaded.hmm_file.path if downloaded else None

    @classmethod
    def _validate_id(cls, source: str, external_id: str) -> bool:
        if source == 'pfam':
            return PfamAPIClient.validate_pfam_id(external_id)
        if source == 'interpro':
            return InterProAPIClient.validate_interpro_id(external_id)
        logger.error(f"Unknown source: {source}")
        return False

    @classmethod
    def _get_from_cache(cls, source: str, external_id: str) -> Optional[ExternalHMMModel]:
        try:
            model = ExternalHMMModel.objects.get(source=source, external_id=external_id.upper())
        except ExternalHMMModel.DoesNotExist:
            return None

        if model.is_expired(days=cls.DEFAULT_TTL_DAYS):
            logger.info(f"Cached model {source}:{external_id} expired - will re-download")
            model.delete()
            return None
        if not os.path.exists(model.hmm_file.path):
            logger.warning(f"Cached file missing for {source}:{external_id} - will re-download")
            model.delete()
            return None
        return model

    @classmethod
    def _download_and_cache(cls, source: str, external_id: str) -> Optional[ExternalHMMModel]:
        log = HMMDownloadLog.objects.create(
            source=source,
            external_id=external_id.upper(),
            status='downloading',
        )

        def _fail(message):
            log.status = 'failed'
            log.error_message = message
            log.completed_at = timezone.now()
            log.save()

        try:
            hmm_content, metadata = cls._fetch_from_api(source, external_id)
            if not hmm_content:
                _fail("Failed to download HMM content from API")
                return None

            model = cls._save_to_cache(source, external_id.upper(), hmm_content, metadata)
            log.status = 'success'
            log.completed_at = timezone.now()
            log.hmm_model = model
            log.save()
            logger.info(f"Successfully cached {source}:{external_id}")
            return model

        except RemoteUnavailable as e:
            logger.warning(f"Remote unavailable while fetching {source}:{external_id}: {e}")
            _fail(f"Remote unavailable: {e}")
            raise
        except Exception as e:
            logger.error(f"Failed to download and cache {source}:{external_id}: {e}")
            _fail(str(e))
            return None

    @classmethod
    def _fetch_from_api(cls, source: str, external_id: str) -> Tuple[Optional[bytes], dict]:
        if source == 'pfam':
            client = PfamAPIClient
        elif source == 'interpro':
            client = InterProAPIClient
        else:
            return None, {}
        return client.download_hmm(external_id), client.get_entry_metadata(external_id) or {}

    @classmethod
    def _save_to_cache(cls, source: str, external_id: str, hmm_content: bytes, metadata: dict) -> ExternalHMMModel:
        if source == 'interpro':
            pfam_members = InterProAPIClient.get_pfam_members(external_id)
            has_pfam_model = bool(pfam_members)
        else:
            pfam_members = []
            has_pfam_model = True

        model = ExternalHMMModel.objects.create(
            source=source,
            external_id=external_id,
            name=metadata.get('name', ''),
            description=metadata.get('description', ''),
            version=metadata.get('version', ''),
            file_size=len(hmm_content),
            checksum=hashlib.sha256(hmm_content).hexdigest(),
            expires_at=timezone.now() + timedelta(days=cls.DEFAULT_TTL_DAYS),
            api_url=metadata.get('download_url', ''),
            has_pfam_model=has_pfam_model,
            pfam_members=pfam_members,
        )
        model.hmm_file.save(f"{external_id}.hmm", ContentFile(hmm_content), save=True)
        return model

    @classmethod
    def cleanup_expired(cls) -> int:
        expired = ExternalHMMModel.objects.filter(expires_at__lt=timezone.now())
        count = expired.count()
        expired.delete()
        logger.info(f"Cleaned up {count} expired HMM cache entries")
        return count

    @classmethod
    def cleanup_old(cls, days: int = 180) -> int:
        threshold = timezone.now() - timedelta(days=days)
        old = ExternalHMMModel.objects.filter(downloaded_at__lt=threshold)
        count = old.count()
        old.delete()
        logger.info(f"Cleaned up {count} old HMM cache entries (downloaded >{days} days ago)")
        return count

    @classmethod
    def get_cache_stats(cls) -> dict:
        models = ExternalHMMModel.objects.all()
        total_size = sum(m.file_size for m in models)
        return {
            'total_models': models.count(),
            'total_size_mb': round(total_size / (1024 * 1024), 2),
            'pfam_count': ExternalHMMModel.objects.filter(source='pfam').count(),
            'interpro_count': ExternalHMMModel.objects.filter(source='interpro').count(),
        }

    @classmethod
    def search_hmm(cls, source: str, query: str, max_results: int = 10) -> list:
        api_limit = max_results * 3 if source == 'interpro' else max_results

        if source == 'pfam':
            api_results = PfamAPIClient.search_by_name(query, api_limit)
        elif source == 'interpro':
            api_results = InterProAPIClient.search_by_name(query, api_limit)
        else:
            return []

        results = []
        for result in api_results:
            if source == 'interpro' and not result.get('has_pfam_model', False):
                continue
            results.append(result)
            if len(results) >= max_results:
                break
        return results
