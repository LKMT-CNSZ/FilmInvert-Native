"""Stable data paths: no writes under an installation, portable/fallback/explicit profiles."""
from pathlib import Path
import os,sys,tempfile
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import app_paths
with tempfile.TemporaryDirectory() as temporary:
    root=Path(temporary);bundle=root/'中文 安装与便携';bundle.mkdir()
    local=root/'用户 空间'
    with patch.dict(os.environ,{'LOCALAPPDATA':str(local)}):
        assert app_paths.select_profile(bundle)==bundle/'data'
        assert app_paths.default_output(bundle,bundle/'data')==bundle/'out'
        (bundle/'installed-layout.json').write_text('{"version":"3.0.0"}')
        original=app_paths.writable
        def no_install_write(path):
            assert not Path(path).is_relative_to(bundle)
            return original(path)
        with patch.object(app_paths,'writable',no_install_write):
            profile=app_paths.select_profile(bundle)
            assert profile==local/'FilmInvertNative/data'
            assert app_paths.default_output(bundle,profile)==local/'FilmInvertNative/exports'
        (bundle/'installed-layout.json').unlink()
        def denied_portable(path):
            if Path(path).is_relative_to(bundle):raise PermissionError('read only test')
            return original(path)
        with patch.object(app_paths,'writable',denied_portable):
            assert app_paths.select_profile(bundle)==profile
            assert app_paths.default_output(bundle,profile)==local/'FilmInvertNative/exports'
        explicit=root/'指定 独立配置'
        assert app_paths.select_profile(bundle,explicit)==explicit
        def denied_all(path):raise PermissionError('unwritable explicit profile')
        with patch.object(app_paths,'writable',denied_all):
            try:app_paths.select_profile(bundle,explicit)
            except PermissionError:pass
            else:raise AssertionError('silently changed explicit profile')
print('PASS installed data/output outside app, Unicode/space portable path, write-probe fallback and explicit profile failure')
