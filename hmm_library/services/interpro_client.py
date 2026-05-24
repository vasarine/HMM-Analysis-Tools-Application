import logging
import re
from typing import Any, Dict, Optional

import requests

from .exceptions import RemoteUnavailable

logger = logging.getLogger(__name__)

INTERPRO_ID_RE = re.compile(r'^IPR\d{6}$')


def _name_and_description(name_field):
    if isinstance(name_field, dict):
        return name_field.get('name', ''), name_field.get('short', '')
    return name_field, name_field


def _extract_pfam_accessions(member_databases):
    pfam = (member_databases or {}).get('pfam', [])
    if isinstance(pfam, dict):
        return list(pfam.keys())
    if isinstance(pfam, list):
        return [m['accession'] for m in pfam if isinstance(m, dict) and m.get('accession')]
    return []


class InterProAPIClient:
    BASE_URL = "https://www.ebi.ac.uk/interpro/api"
    TIMEOUT = 30

    @staticmethod
    def validate_interpro_id(interpro_id: str) -> bool:
        return bool(INTERPRO_ID_RE.match(interpro_id.upper()))

    @classmethod
    def get_entry_metadata(cls, interpro_id: str) -> Optional[Dict[str, Any]]:
        if not cls.validate_interpro_id(interpro_id):
            logger.error(f"Invalid InterPro ID format: {interpro_id}")
            return None

        url = f"{cls.BASE_URL}/entry/interpro/{interpro_id.upper()}/"
        try:
            response = requests.get(url, timeout=cls.TIMEOUT)
            response.raise_for_status()
            meta = response.json().get('metadata', {})
        except requests.exceptions.RequestException as e:
            logger.error(f"InterPro unreachable while fetching metadata for {interpro_id}: {e}")
            raise RemoteUnavailable(f"InterPro API unreachable: {e}") from e
        except (KeyError, ValueError) as e:
            logger.error(f"Failed to parse InterPro response for {interpro_id}: {e}")
            return None

        name, description = _name_and_description(meta.get('name', ''))
        return {
            'accession': meta.get('accession'),
            'name': name,
            'description': description,
            'type': meta.get('type'),
            'member_databases': meta.get('member_databases', {}),
        }

    @classmethod
    def get_pfam_members(cls, interpro_id: str) -> list:
        metadata = cls.get_entry_metadata(interpro_id)
        if not metadata:
            return []
        return _extract_pfam_accessions(metadata.get('member_databases', {}))

    @classmethod
    def download_hmm(cls, interpro_id: str) -> Optional[bytes]:
        if not cls.validate_interpro_id(interpro_id):
            logger.error(f"Invalid InterPro ID format: {interpro_id}")
            return None

        pfam_members = cls.get_pfam_members(interpro_id)
        if not pfam_members:
            logger.warning(f"No Pfam members found for InterPro {interpro_id}")
            return None

        from .pfam_client import PfamAPIClient
        pfam_id = pfam_members[0]
        logger.info(f"Downloading HMM for InterPro {interpro_id} via Pfam {pfam_id}")
        return PfamAPIClient.download_hmm(pfam_id)

    @classmethod
    def search_by_name(cls, query: str, max_results: int = 10) -> list:
        url = f"{cls.BASE_URL}/entry/interpro/"
        try:
            response = requests.get(
                url,
                params={'search': query, 'page_size': max_results},
                timeout=cls.TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()
        except requests.exceptions.Timeout:
            logger.warning(f"InterPro search timeout for '{query}' (> {cls.TIMEOUT}s)")
            return []
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to search InterPro for '{query}': {e}")
            return []
        except (KeyError, ValueError) as e:
            logger.error(f"Failed to parse InterPro search results: {e}")
            return []

        results = []
        for entry in data.get('results', [])[:max_results]:
            meta = entry.get('metadata', {})
            name, description = _name_and_description(meta.get('name', ''))
            pfam_ids = _extract_pfam_accessions(meta.get('member_databases', {}))
            results.append({
                'accession': meta.get('accession'),
                'name': name,
                'description': description,
                'type': meta.get('type'),
                'pfam_members': pfam_ids,
                'has_pfam_model': bool(pfam_ids),
            })
        return results

    @classmethod
    def get_hmm_info(cls, interpro_id: str) -> Optional[Dict[str, Any]]:
        metadata = cls.get_entry_metadata(interpro_id)
        if not metadata:
            return None
        metadata['pfam_members'] = cls.get_pfam_members(interpro_id)
        return metadata
