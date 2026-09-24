"""Create demo data for local development. Refuses to run unless DEBUG is True."""

import io
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from PIL import Image, ImageDraw

from accounts.roles import EVENT_ADMIN, MODERATOR, ensure_roles
from events.models import Award, Category, Event, EventStatus
from nominations.models import Nomination, NominationStatus, Nominee

User = get_user_model()

DEMO_PASSWORD = "demo-password-123"  # local development only

# category -> award -> nominee stage names
CATALOGUE = {
    "Music": {
        "Artist of the Year": ["Amani Wanjiku", "Baraka Otieno", "Cynthia Njeri"],
        "Best New Artist": ["Dennis Kamau", "Esther Achieng"],
    },
    "Film & TV": {
        "Best Actor": ["Faraja Mwangi", "Gideon Kiprop", "Halima Yusuf"],
        "Best Actress": ["Irene Wairimu", "Joy Naliaka"],
    },
    "Comedy": {
        "Comedian of the Year": ["Kevin Omondi", "Lucy Wambui", "Moses Kariuki"],
        "Best Skit": ["Nancy Atieno", "Oscar Mutua"],
    },
}
COLOURS = [
    (196, 69, 54),
    (52, 152, 219),
    (39, 174, 96),
    (142, 68, 173),
    (230, 126, 34),
    (44, 62, 80),
]


def placeholder_photo(text, index):
    image = Image.new("RGB", (320, 320), COLOURS[index % len(COLOURS)])
    draw = ImageDraw.Draw(image)
    initials = "".join(part[0] for part in text.split()[:2]).upper()
    draw.text((140, 150), initials, fill=(255, 255, 255))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return ContentFile(buffer.getvalue(), name="placeholder.png")


class Command(BaseCommand):
    help = (
        "Create one event, 3 categories, 6 awards, 15 approved nominees, the role groups and "
        "one user per role. Local development only (DEBUG must be True). Safe to re-run."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--password", default=DEMO_PASSWORD, help="Password for the demo users."
        )
        parser.add_argument(
            "--no-photos", action="store_true", help="Skip generating placeholder photos."
        )

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError(
                "seed_demo refuses to run with DEBUG=False: it is for local development only."
            )
        with transaction.atomic():
            ensure_roles()
            users = self._users(options["password"])
            event = self._event()
            count = self._catalogue(event, with_photos=not options["no_photos"])
        self.stdout.write(
            self.style.SUCCESS(f"Seeded '{event.name}' ({event.slug}) with {count} nominees.")
        )
        self.stdout.write("Demo staff logins (local development only):")
        for role, user in users.items():
            self.stdout.write(f"  {role:<11} {user.email} / {options['password']}")

    def _users(self, password):
        specs = {
            "superuser": ("admin@example.com", "Demo Superuser", None, True),
            "moderator": ("moderator@example.com", "Demo Moderator", MODERATOR, False),
            "eventadmin": ("eventadmin@example.com", "Demo Event Admin", EVENT_ADMIN, False),
        }
        users = {}
        for key, (email, name, role, superuser) in specs.items():
            user, created = User.objects.get_or_create(
                email=email,
                defaults={"full_name": name, "is_staff": True, "is_superuser": superuser},
            )
            user.set_password(password)
            user.save()
            if role:
                user.groups.set([Group.objects.get(name=role)])
            users[key] = user
        return users

    def _event(self):
        now = timezone.now()
        event, _ = Event.objects.update_or_create(
            slug="kw-awards-2026",
            defaults={
                "name": "KW Awards 2026",
                "year": 2026,
                "status": EventStatus.VOTING_OPEN,
                "nominations_open_at": now - timedelta(days=60),
                "nominations_close_at": now - timedelta(days=30),
                "voting_opens_at": now - timedelta(days=1),
                "voting_closes_at": now + timedelta(days=30),
            },
        )
        return event

    def _catalogue(self, event, with_photos):
        nominees = 0
        for c_order, (category_name, awards) in enumerate(CATALOGUE.items()):
            category, _ = Category.objects.get_or_create(
                event=event,
                slug=category_name.lower().replace(" & ", "-").replace(" ", "-"),
                defaults={"name": category_name, "display_order": c_order},
            )
            for a_order, (award_name, names) in enumerate(awards.items()):
                award, _ = Award.objects.get_or_create(
                    category=category,
                    slug=award_name.lower().replace(" ", "-"),
                    defaults={"name": award_name, "display_order": a_order},
                )
                for name in names:
                    nominee, created = Nominee.objects.get_or_create(
                        name=name,
                        defaults={
                            "stage_name": name.split()[0],
                            "bio": f"{name} is a nominee for {award_name} at the KW Awards 2026.",
                            "contact_email": f"{name.split()[0].lower()}@example.com",
                            "instagram_url": f"https://instagram.com/{name.split()[0].lower()}",
                        },
                    )
                    if with_photos and not nominee.photo:
                        nominee.photo.save("p.png", placeholder_photo(name, nominees), save=True)
                    Nomination.objects.get_or_create(
                        nominee=nominee,
                        award=award,
                        defaults={
                            "status": NominationStatus.APPROVED,
                            "reviewed_at": timezone.now(),
                        },
                    )
                    nominees += 1
        return nominees
