from curator.domain.models import FolderSnapshot


def test_appledouble_files_are_not_tiffs():
    snap = FolderSnapshot(
        path="p",
        files=("a.tif", "._a.tif", "b.TIFF", "._b.TIFF"),
        archive_dir="ИСХ",
        archive_files=("._c.tif", "c.tif"),
    )
    assert snap.tiffs() == ("a.tif", "b.TIFF")
    assert snap.archive_tiffs() == ("c.tif",)
    assert snap.tiff_count() == 3
