from app.db.models import JobKind
from app.importers.base import Importer
from app.importers.text import TextImporter
from app.importers.voice import VoiceImporter

IMPORTERS: dict[JobKind, Importer] = {
    JobKind.text: TextImporter(),
    JobKind.voice: VoiceImporter(),
}
