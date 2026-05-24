import logging

logger = logging.getLogger(__name__)


def detect_hmm_source(external_id: str):
    from hmm_library.services.pfam_client import PfamAPIClient
    from hmm_library.services.interpro_client import InterProAPIClient

    ext = external_id.upper().strip()
    if PfamAPIClient.validate_pfam_id(ext):
        return 'pfam'
    if InterProAPIClient.validate_interpro_id(ext):
        return 'interpro'
    return None


def resolve_external_hmm(source: str, external_id: str):
    from hmm_library.services import HMMCacheManager
    from hmm_library.services.exceptions import RemoteUnavailable
    from hmm_library.services.pfam_client import PfamAPIClient
    from hmm_library.services.interpro_client import InterProAPIClient

    if source not in ('pfam', 'interpro'):
        raise ValueError(
            f'Unknown HMM source {source!r}. Expected "pfam" or "interpro".'
        )

    external_id = external_id.upper().strip()
    src_label = 'Pfam' if source == 'pfam' else 'InterPro'

    if source == 'pfam' and not PfamAPIClient.validate_pfam_id(external_id):
        raise ValueError(
            f'Invalid Pfam ID format: {external_id!r}. '
            'Expected format: PF followed by 5 digits (e.g. PF00001).'
        )
    if source == 'interpro' and not InterProAPIClient.validate_interpro_id(external_id):
        raise ValueError(
            f'Invalid InterPro ID format: {external_id!r}. '
            'Expected format: IPR followed by 6 digits (e.g. IPR000001).'
        )

    logger.info('Resolving HMM from %s: %s', source, external_id)

    try:
        hmm_path = HMMCacheManager.get_or_download(source, external_id)
    except RemoteUnavailable as exc:
        raise ValueError(
            f'{src_label} API is currently unreachable. '
            f'The HMM for {external_id} was not downloaded - '
            'please try again in a few moments.'
        ) from exc

    if not hmm_path:
        if source == 'interpro':
            raise ValueError(
                f'Could not find a Pfam HMM model for {external_id}. '
                'This InterPro entry has no associated Pfam model - '
                'try a different InterPro entry, or use a Pfam ID (PF00001) directly.'
            )
        raise ValueError(
            f'Could not download HMM for {external_id}. '
            'Please check the ID and try again.'
        )

    hmm_name = None
    try:
        from hmm_library.models import ExternalHMMModel
        cached = ExternalHMMModel.objects.filter(
            source=source, external_id=external_id
        ).first()
        if cached:
            hmm_name = cached.name
    except Exception:
        logger.debug('Could not retrieve cached HMM name for %s', external_id)

    logger.info('Resolved HMM: %s (name: %s)', hmm_path, hmm_name)
    return hmm_path, hmm_name
