import pytest

from ocr_common.testing import auth_headers, make_client, set_test_env

set_test_env(BLUR_BACKEND="mock", AUTH_DISABLED="false")

from app.config import get_settings  # noqa: E402
from app.dependencies import get_blur_service  # noqa: E402
from app.main import app  # noqa: E402
from app.services.blur_service import BlurService  # noqa: E402


@pytest.fixture(scope="session")
def client():
    return make_client(app)


@pytest.fixture
def auth() -> dict[str, str]:
    return auth_headers()


@pytest.fixture
def use_check():
    """Layani route dengan model ini, bukan backend yang dikonfigurasi."""

    def _use(model, **settings):
        configured = get_settings().model_copy(update=settings) if settings else get_settings()
        app.dependency_overrides[get_blur_service] = lambda: BlurService(model, configured)

    yield _use
    app.dependency_overrides.pop(get_blur_service, None)
