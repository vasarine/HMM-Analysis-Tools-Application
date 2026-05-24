import gzip
import logging
import re
from typing import Any, Dict, Optional

import requests

from .exceptions import RemoteUnavailable

logger = logging.getLogger(__name__)

PFAM_ID_RE = re.compile(r'^PF\d{5}$')


class PfamAPIClient:
    BASE_URL = "https://www.ebi.ac.uk/interpro/api"
    TIMEOUT = 30

    @staticmethod
    def validate_pfam_id(pfam_id: str) -> bool:
        return bool(PFAM_ID_RE.match(pfam_id.upper()))

    @classmethod
    def get_entry_metadata(cls, pfam_id: str) -> Optional[Dict[str, Any]]:
        if not cls.validate_pfam_id(pfam_id):
            logger.error(f"Invalid Pfam ID format: {pfam_id}")
            return None

        url = f"{cls.BASE_URL}/entry/pfam/{pfam_id.upper()}/"
        try:
            response = requests.get(url, timeout=cls.TIMEOUT)
            response.raise_for_status()
            metadata = response.json().get('metadata', {})
            return {
                'accession': metadata.get('accession'),
                'name': metadata.get('name', {}).get('name', ''),
                'description': metadata.get('name', {}).get('short', ''),
                'type': metadata.get('type'),
                'member_databases': metadata.get('member_databases'),
            }
        except requests.exceptions.RequestException as e:
            logger.error(f"Pfam API unreachable while fetching metadata for {pfam_id}: {e}")
            raise RemoteUnavailable(f"Pfam API unreachable: {e}") from e
        except (KeyError, ValueError) as e:
            logger.error(f"Failed to parse Pfam response for {pfam_id}: {e}")
            return None

    @classmethod
    def download_hmm(cls, pfam_id: str) -> Optional[bytes]:
        if not cls.validate_pfam_id(pfam_id):
            logger.error(f"Invalid Pfam ID format: {pfam_id}")
            return None

        url = f"{cls.BASE_URL}/entry/pfam/{pfam_id.upper()}/?annotation=hmm"
        try:
            response = requests.get(url, timeout=cls.TIMEOUT)
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            logger.error(f"Pfam API unreachable while downloading HMM for {pfam_id}: {e}")
            raise RemoteUnavailable(f"Pfam API unreachable: {e}") from e

        content = response.content
        try:
            content = gzip.decompress(content)
        except gzip.BadGzipFile:
            pass

        if not content or not content.startswith(b'HMMER'):
            logger.error(f"Invalid HMM content for {pfam_id}. First 100 bytes: {content[:100]}")
            return None

        logger.info(f"Downloaded HMM for {pfam_id} ({len(content)} bytes)")
        return content

    @classmethod
    def search_by_name(cls, query: str, max_results: int = 10) -> list:
        url = f"{cls.BASE_URL}/entry/pfam/"
        try:
            response = requests.get(
                url,
                params={'search': query, 'page_size': max_results},
                timeout=cls.TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()
        except requests.exceptions.Timeout:
            logger.warning(f"Pfam search timeout for '{query}' (> {cls.TIMEOUT}s)")
            return []
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to search Pfam for '{query}': {e}")
            return []
        except (KeyError, ValueError) as e:
            logger.error(f"Failed to parse Pfam search results: {e}")
            return []

        return [
            {
                'accession': m.get('accession', ''),
                'name': m.get('name', ''),
                'description': m.get('name', ''),
                'type': m.get('type', ''),
            }
            for m in (entry.get('metadata', {}) for entry in data.get('results', [])[:max_results])
        ]

    @classmethod
    def get_hmm_info(cls, pfam_id: str) -> Optional[Dict[str, Any]]:
        metadata = cls.get_entry_metadata(pfam_id)
        if not metadata:
            return None
        metadata['download_url'] = f"{cls.BASE_URL}/entry/pfam/{pfam_id.upper()}/?annotation=hmm"
        return metadata
