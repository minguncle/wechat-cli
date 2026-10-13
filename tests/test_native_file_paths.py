import pytest
from novus_wechat.native_file_paths import verify_cache,validate_names


def test_file_cache_is_bound_to_account_request_and_filename(tmp_path):
    source=tmp_path/'account'/'db_storage'
    source.mkdir(parents=True)
    cache=source.parent/'cache'
    cache.mkdir()
    directory='novus-'+'a'*32
    filename='中文测试.txt'
    correct=cache/'2026-09'/'chatbot'/'file'/directory/filename
    assert verify_cache(source,directory,filename,correct)==correct
    for wrong in [tmp_path/'other'/'cache'/'2026-09'/'chatbot'/'file'/directory/filename,
                  cache/'2026-13'/'chatbot'/'file'/directory/filename,
                  correct.with_name('other.txt'),correct.parent.parent/'novus-other'/filename]:
        with pytest.raises(ValueError):
            verify_cache(source,directory,filename,wrong)


@pytest.mark.parametrize('filename',['','..','../x.txt','C:\\x.txt','a/b','a\0b'])
def test_reject_path_in_filename(filename):
    with pytest.raises(ValueError):
        validate_names('novus-'+'b'*32,filename)
