"""Setelan LLM dan prompt dari environment benar-benar sampai ke lapisan riset.

Yang dijaga di sini bukan "fungsinya dipanggil", melainkan bahwa penimpaannya BERPENGARUH. Tujuh
setelan dibekukan jadi konstanta modul saat `s2_structure` diimpor, dan `slip_ml.structure`
membekukan model bawaan satu lagi di atasnya. Memutasi dict setelan saja akan lolos tanpa suara
untuk backend, lalu diam-diam tidak berpengaruh untuk prompt dan model — kegagalan termahal,
karena service tetap menjawab dan jawabannya tetap kelihatan masuk akal.

Dilewati kalau `slip_ml` tidak ada di image.
"""

import pytest

runtime = pytest.importorskip("slip_ml.runtime")


@pytest.fixture(autouse=True)
def pulihkan():
    """Kembalikan setelan global setelah tiap tes: modul-modul ini dipakai bersama seluruh berkas
    tes di service ini, dan satu penimpaan yang tertinggal akan muncul sebagai kegagalan di tempat
    yang sama sekali tidak berhubungan."""
    import s2_structure as structure
    from core import config

    semula = (
        dict(config.LLM),
        config.PROMPT,
        dict(config.PROMPT_META),
        structure.LLM_PROMPT,
        structure.LLM_MODEL,
        structure.LLM_BACKEND,
    )
    yield
    config.LLM.clear()
    config.LLM.update(semula[0])
    config.PROMPT, config.PROMPT_META = semula[1], semula[2]
    structure.LLM_PROMPT, structure.LLM_MODEL, structure.LLM_BACKEND = semula[3], semula[4], semula[5]


def test_the_registered_backends_are_the_three_the_settings_name():
    assert runtime.backends() == ("bedrock", "http", "off")


def test_the_database_prompt_source_is_registered_by_the_service():
    """`PROMPT_SOURCE=db` didaftarkan service saat backend dibangun (app/prompts.py)."""
    from app.prompts import register_db_prompt_source

    register_db_prompt_source(database_url=None, table="prompts", fallback_path=None, fallback=False)
    assert runtime.prompt_sources() == ("db", "file")


def test_switching_the_backend_reaches_both_the_live_dict_and_the_frozen_constant():
    import s2_structure as structure
    from core import config

    runtime.configure(llm={"backend": "off"})

    assert config.LLM["backend"] == "off"  # dibaca tiap panggilan
    assert structure.LLM_BACKEND == "off"  # dibekukan saat impor


def test_a_new_model_also_refreshes_the_default_the_caller_falls_back_to():
    """`SlipRulesStructurer` memakai `DEFAULT_LLM_MODEL` ketika pemanggil tidak menyebut model.
    Dibiarkan basi, setelan LLM_MODEL akan tidak berpengaruh justru di jalur yang paling sering."""
    import s2_structure as structure

    from slip_ml import structure as slip_structure

    runtime.configure(llm={"model": "amazon.nova-lite-v1:0"})

    assert structure.LLM_MODEL == "amazon.nova-lite-v1:0"
    assert slip_structure.DEFAULT_LLM_MODEL == "amazon.nova-lite-v1:0"


def test_an_unknown_backend_is_refused_with_the_registered_names():
    with pytest.raises(ValueError) as exc:
        runtime.configure(llm={"backend": "gpt-tetangga"})

    assert "bedrock" in str(exc.value) and "http" in str(exc.value)


def test_the_http_backend_without_an_endpoint_is_refused():
    """Ditolak saat start, bukan saat permintaan pertama: salah setelan harus terlihat saat deploy."""
    with pytest.raises(ValueError, match="endpoint"):
        runtime.configure(llm={"backend": "http"})


def test_the_prompt_pointer_can_name_a_file_outside_the_image(tmp_path):
    """Path absolut itu yang membuat prompt bisa dipasang sebagai volume/ConfigMap, tanpa rebuild."""
    import s2_structure as structure
    from core import config

    berkas = tmp_path / "slip_gaji.v9.md"
    berkas.write_text("PROMPT LAIN {n} {fields} {text}", encoding="utf-8")

    laporan = runtime.configure(prompt={"source": "file", "name": "slip_gaji", "version": 9, "path": str(berkas)})

    assert structure.LLM_PROMPT.startswith("PROMPT LAIN")
    assert config.PROMPT_META == {"source": "file", "name": "slip_gaji", "version": 9}
    assert laporan["prompt"]["version"] == 9


def test_an_empty_prompt_is_refused_rather_than_served(tmp_path):
    """Prompt kosong tidak menimbulkan galat saat dipakai — modelnya hanya menjawab lebih buruk,
    dan tidak ada yang memberi tahu. Jadi ditolak di sini."""
    berkas = tmp_path / "kosong.md"
    berkas.write_text("   \n\n", encoding="utf-8")

    with pytest.raises(ValueError, match="kosong"):
        runtime.configure(prompt={"source": "file", "path": str(berkas)})


def test_a_prompt_file_that_is_not_there_is_refused(tmp_path):
    with pytest.raises(FileNotFoundError):
        runtime.configure(prompt={"source": "file", "path": str(tmp_path / "tidak-ada.md")})


def test_settings_left_unset_change_nothing():
    """Semua None berarti "pakai config.yaml bawaan image" — ini yang membuat service tanpa setelan
    tambahan tetap berjalan seperti sebelumnya."""
    sebelum = runtime.state()

    laporan = runtime.configure(
        llm={"backend": None, "endpoint": None, "model": None},
        prompt={"source": None, "name": None, "version": None, "path": None},
    )

    assert laporan["llm"] == {}
    assert runtime.state() == sebelum


def test_the_state_reports_what_is_actually_in_force():
    """Dipakai untuk log saat start: prompt yang salah versi tetap menghasilkan jawaban yang
    kelihatan benar, jadi satu-satunya cara mengetahuinya adalah dari laporan ini."""
    keadaan = runtime.state()

    assert keadaan["prompt"]["source"] == "file"
    assert keadaan["prompt_chars"] > 0
    assert keadaan["llm_backend"] in runtime.backends()
