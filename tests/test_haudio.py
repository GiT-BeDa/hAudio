import importlib.util
import os
from pathlib import Path

from fastapi.testclient import TestClient


MODULE_PATH = Path(__file__).parents[1] / 'opt' / 'haudio' / 'haudio_main.py'
spec = importlib.util.spec_from_file_location('haudio_main_under_test', MODULE_PATH)
haudio = importlib.util.module_from_spec(spec)
spec.loader.exec_module(haudio)


def test_only_haudio_loopbacks_are_selected_for_cleanup():
    output = (
        '12\tmodule-loopback\tsource=mic sink=headset\n'
        '13\tmodule-loopback\tsource=HAUDIO_SOUNDBOARD.monitor sink=x\n'
        '14\tmodule-null-sink\tsink_name=HAUDIO_SOUNDBOARD\n'
        '15\tmodule-loopback\tsource=x sink=y HAUDIO_PC1_IN\n'
    )
    assert haudio.haudio_loopback_ids(output) == ['13', '15']


def test_unassigned_roles_do_not_use_hardware_fallbacks():
    cards = [{'id': 'usb-1', 'suffix': 'card_1', 'product': 'Any USB Audio',
              'description': 'Any USB Audio', 'source': 'source_1', 'sink': 'sink_1'}]
    old = haudio.state.get('assignments')
    haudio.state['assignments'] = {}
    try:
        assert haudio.selected_card('pc1', cards) is None
        assert haudio.selected_card('pc2', cards) is None
        assert haudio.selected_card('headset', cards) is None
    finally:
        haudio.state['assignments'] = old or {}


def test_explicit_assignment_selects_card_by_physical_id():
    cards = [{'id': 'usb-1.2', 'suffix': 'card_1', 'product': 'Any USB Audio',
              'description': 'Any USB Audio', 'source': 'source_1', 'sink': 'sink_1'}]
    old = haudio.state.get('assignments')
    haudio.state['assignments'] = {'pc1': 'usb-1.2'}
    try:
        assert haudio.selected_card('pc1', cards) == cards[0]
    finally:
        haudio.state['assignments'] = old or {}


def test_filename_validation_allows_apostrophe_but_blocks_paths():
    assert haudio.valid_sound_filename("meeting's intro.mp3")
    assert not haudio.valid_sound_filename('../escape.mp3')
    assert haudio.valid_recording_filename('session 01.opus')
    assert not haudio.valid_recording_filename('../session.opus')


def test_audio_environment_uses_process_runtime_by_default():
    env = haudio.audio_env()
    assert env['XDG_RUNTIME_DIR'] == haudio.RUNTIME_DIR
    assert env['PULSE_SERVER'] == haudio.PULSE_SERVER


def test_requirements_and_service_use_generic_runtime_configuration():
    requirements = (MODULE_PATH.parents[2] / 'requirements.txt').read_text()
    service = (MODULE_PATH.parents[2] / 'etc' / 'systemd' / 'system' / 'haudio-control.service').read_text()
    assert 'fastapi' in requirements.lower()
    assert 'uvicorn' in requirements.lower()
    assert 'User=haudio' in service
    assert 'XDG_RUNTIME_DIR=/run/user/1000' not in service


def test_api_documentation_covers_controls_and_soundboard_volume():
    docs = (MODULE_PATH.parents[2] / 'docs' / 'API.md').read_text()
    assert 'POST` | `/api/soundboard/volume' in docs
    assert '`/api/status`' in docs
    assert '`/ws`' in docs


def test_frontend_is_served_as_separate_static_assets():
    client = TestClient(haudio.APP)

    index = client.get('/')
    assert index.status_code == 200
    assert 'hAudio 0.01' in index.text
    assert '/static/app.js' in index.text
    assert '/static/style.css' in index.text

    javascript = client.get('/static/app.js')
    stylesheet = client.get('/static/style.css')
    assert javascript.status_code == 200
    assert stylesheet.status_code == 200
    assert 'function api' in javascript.text
    assert '.card' in stylesheet.text


def test_public_package_does_not_embed_the_old_html_page():
    source = MODULE_PATH.read_text()
    assert "HTML='''" not in source
    assert 'StaticFiles' in source
