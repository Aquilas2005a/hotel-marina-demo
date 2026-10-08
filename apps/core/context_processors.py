from django.conf import settings
from django.db.models import Q
from django.utils import timezone
from .models import HotelProfile, NavigationLink, SiteNotice, SocialLink


def hotel(request):
    """Rend le profil de l’hôtel disponible, y compris dans les vues Django intégrées."""
    today = timezone.localdate()
    return {
        "hotel": HotelProfile.objects.first(),
        "fedapay_enabled": settings.FEDAPAY_ENABLED,
        "social_links": SocialLink.objects.filter(is_active=True),
        "site_notices": SiteNotice.objects.filter(is_active=True, starts_on__lte=today).filter(Q(ends_on__isnull=True) | Q(ends_on__gte=today)),
        "navigation_links": NavigationLink.objects.filter(is_active=True),
    }
