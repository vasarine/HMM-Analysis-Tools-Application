from django.core.management.base import BaseCommand
from hmm_library.services import HMMCacheManager


class Command(BaseCommand):
    help = 'Clean up expired HMM cache and old models'

    def add_arguments(self, parser):
        parser.add_argument(
            '--old-days',
            type=int,
            default=180,
            help='How many days old model should be deleted (default: 180)'
        )

    def handle(self, *args, **options):
        old_days = options['old_days']

        self.stdout.write(self.style.WARNING('Starting HMM cache cleanup...'))

        expired_count = HMMCacheManager.cleanup_expired()
        self.stdout.write(
            self.style.SUCCESS(f'Deleted expired models: {expired_count}')
        )

        old_count = HMMCacheManager.cleanup_old(days=old_days)
        self.stdout.write(
            self.style.SUCCESS(f'Deleted old models (>{old_days}d): {old_count}')
        )

        stats = HMMCacheManager.get_cache_stats()
        self.stdout.write('\n' + self.style.WARNING('Cache statistics:'))
        self.stdout.write(f"  Total models: {stats['total_models']}")
        self.stdout.write(f"  Total size: {stats['total_size_mb']} MB")
        self.stdout.write(f"  Pfam models: {stats['pfam_count']}")
        self.stdout.write(f"  InterPro models: {stats['interpro_count']}")

        total_cleaned = expired_count + old_count
        self.stdout.write(
            '\n' + self.style.SUCCESS(f'Cleanup completed! Total deleted: {total_cleaned}')
        )
