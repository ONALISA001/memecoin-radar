"""DATABASE_URL copy-paste tolerance and no password leaks (the first Render deploy crashed on
a pasted  DIRECT_URL="…"  line and the parser printed the password into the host's logs)."""
import pytest

from app.config import DatabaseUrlError, check_db_url, mask_db_url, normalize_db_url

PW = "S3cretPassw0rd"
HOST = "aws-0-eu-central-1.pooler.supabase.com:5432/postgres"
CLEAN = f"postgresql://postgres.abcdef:{PW}@{HOST}?sslmode=require"


@pytest.mark.parametrize("pasted", [
    f'DIRECT_URL="postgresql://postgres.abcdef:{PW}@{HOST}"',          # the line that broke the deploy
    f"DATABASE_URL='postgresql://postgres.abcdef:{PW}@{HOST}'",
    f'export DATABASE_URL="postgresql://postgres.abcdef:{PW}@{HOST}"',
    f'"postgresql://postgres.abcdef:{PW}@{HOST}"',
    f"  postgresql://postgres.abcdef:{PW}@{HOST}  ",
    f"postgres://postgres.abcdef:{PW}@{HOST}",
    f"postgresql://postgres.abcdef:[{PW}]@{HOST}",                      # brackets left around the password
])
def test_pasted_variants_become_a_clean_url(pasted):
    url = normalize_db_url(pasted)
    assert url == CLEAN
    check_db_url(url)                                                   # parses fine


def test_mask_hides_the_password():
    assert mask_db_url(CLEAN) == f"postgresql://postgres.abcdef:***@{HOST}?sslmode=require"
    assert PW not in mask_db_url(CLEAN)
    assert mask_db_url("sqlite:///x.db") == "sqlite:///x.db"


@pytest.mark.parametrize("bad, hint", [
    (f"postgresql://postgres.abcdef:[YOUR-PASSWORD]@{HOST}", "placeholder"),
    (f"mysql://root:{PW}@db/x", "must start with postgresql://"),
    (f"postgresql://postgres.abcdef:{PW}@:5432/postgres", "no host"),
])
def test_bad_urls_explain_without_leaking(bad, hint):
    with pytest.raises(DatabaseUrlError) as e:
        check_db_url(normalize_db_url(bad))
    assert hint in str(e.value)
    assert PW not in str(e.value)


def test_unparseable_url_does_not_leak_password():
    # a raw "@" / "/" inside the password breaks parsing; the error must stay password-free
    raw_pw = "abc/def?ghi"
    with pytest.raises(DatabaseUrlError) as e:
        check_db_url(f"postgresql://postgres.abcdef:{raw_pw}@pooler.supabase.com:notaport/postgres")
    assert raw_pw not in str(e.value) and "***" in str(e.value)


def test_sqlite_is_untouched():
    assert normalize_db_url("sqlite:///radar.db") == "sqlite:///radar.db"
    check_db_url("sqlite:///radar.db")
