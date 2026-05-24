"""
Small JSON endpoint that powers the Pfam/InterPro id autocomplete.
"""

from django.http import JsonResponse
from django.views.decorators.http import require_GET
from .services import HMMCacheManager
from .services.exceptions import RemoteUnavailable


@require_GET
def search_hmm_autocomplete(request):
    source = request.GET.get('source', 'pfam')
    query = request.GET.get('q', '').strip()
    limit = int(request.GET.get('limit', 5))

    if not query or len(query) < 3:
        return JsonResponse({
            'results': [],
            'message': 'Enter at least 3 characters'
        })

    if source not in ['pfam', 'interpro']:
        return JsonResponse({
            'results': [],
            'error': 'Invalid source'
        }, status=400)

    try:
        try:
            results = HMMCacheManager.search_hmm(source, query, max_results=limit)
        except RemoteUnavailable as e:
            return JsonResponse({
                'results': [],
                'remote_unavailable': True,
                'message': f'{source.title()} API temporarily unreachable. Please try again.'
            })

        formatted_results = []
        for result in results:
            if not isinstance(result, dict):
                continue

            accession = result.get('accession', '')
            name = result.get('name', '')
            description = result.get('description', '')

            if not accession:
                continue

            if source == 'interpro':
                has_pfam = result.get('has_pfam_model', False)
                if not has_pfam:
                    continue

            formatted_results.append({
                'id': accession,
                'name': name,
                'description': description,
                'type': result.get('type', ''),
                'label': f"{accession} - {name}",
            })

        return JsonResponse({
            'results': formatted_results,
            'count': len(formatted_results)
        })

    except Exception as e:
        import logging
        import traceback
        logger = logging.getLogger(__name__)
        logger.error(f"Autocomplete search error: {str(e)}")
        logger.error(traceback.format_exc())

        return JsonResponse({
            'results': [],
            'error': str(e)
        }, status=500)


@require_GET
def get_cache_stats(request):
    try:
        stats = HMMCacheManager.get_cache_stats()
        return JsonResponse(stats)
    except Exception as e:
        return JsonResponse({
            'error': str(e)
        }, status=500)
