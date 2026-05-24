from django.core.management.base import BaseCommand
from hmm_library.services import HMMCacheManager


class Command(BaseCommand):
    help = 'Pre-load Pfam/InterPro HMM models into cache'

    def add_arguments(self, parser):
        parser.add_argument(
            'ids',
            nargs='+',
            type=str,
            help='Pfam/InterPro ID list (e.g. PF00001 PF00002 IPR000001)'
        )
        parser.add_argument(
            '--source',
            type=str,
            choices=['auto', 'pfam', 'interpro'],
            default='auto',
            help='HMM source (auto detects by ID format)'
        )

    def handle(self, *args, **options):
        ids = options['ids']
        source_option = options['source']

        self.stdout.write(
            self.style.WARNING(f'Starting pre-loading of {len(ids)} HMM models...\n')
        )

        success_count = 0
        failed_count = 0
        cached_count = 0

        for hmm_id in ids:
            hmm_id = hmm_id.upper()

            if source_option == 'auto':
                if hmm_id.startswith('PF'):
                    source = 'pfam'
                elif hmm_id.startswith('IPR'):
                    source = 'interpro'
                else:
                    self.stdout.write(
                        self.style.ERROR(f'✗ {hmm_id}: Unknown ID format')
                    )
                    failed_count += 1
                    continue
            else:
                source = source_option

            from hmm_library.models import ExternalHMMModel
            existing = ExternalHMMModel.objects.filter(
                source=source,
                external_id=hmm_id
            ).first()

            if existing and not existing.is_expired():
                self.stdout.write(
                    self.style.WARNING(f'○ {hmm_id}: Already in cache')
                )
                cached_count += 1
                continue

            self.stdout.write(f'⌛ {hmm_id}: Downloading...', ending='')

            try:
                file_path = HMMCacheManager.get_or_download(source, hmm_id)

                if file_path:
                    model = ExternalHMMModel.objects.filter(
                        source=source,
                        external_id=hmm_id
                    ).first()

                    name = f" ({model.name})" if model and model.name else ""
                    self.stdout.write(
                        '\r' + self.style.SUCCESS(f'{hmm_id}{name}')
                    )
                    success_count += 1
                else:
                    self.stdout.write(
                        '\r' + self.style.ERROR(f'✗ {hmm_id}: Failed to download')
                    )
                    failed_count += 1

            except Exception as e:
                self.stdout.write(
                    '\r' + self.style.ERROR(f'✗ {hmm_id}: {str(e)}')
                )
                failed_count += 1

        self.stdout.write('\n' + self.style.WARNING('Pre-loading completed:'))
        self.stdout.write(
            self.style.SUCCESS(f'  Successfully: {success_count}')
        )
        self.stdout.write(
            self.style.WARNING(f'  Already in cache: {cached_count}')
        )
        if failed_count > 0:
            self.stdout.write(
                self.style.ERROR(f'  Failed: {failed_count}')
            )

        stats = HMMCacheManager.get_cache_stats()
        self.stdout.write(
            f'\nTotal in cache: {stats["total_models"]} models ({stats["total_size_mb"]} MB)'
        )
