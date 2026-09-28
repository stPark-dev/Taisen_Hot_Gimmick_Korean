"""Supported source profile: MAME set hotgmck "Taisen Hot Gimmick (Japan)" (romset good in MAME 0.289)."""
import hashlib
import pathlib
import zipfile


class SourceError(RuntimeError):
    pass


PROFILE = {
    "1-u22.bin": (524288, "6ea7bd18bcf6224ed9b0480bb59c684f13b71d8a"),
    "2-u23.bin": (524288, "bb4f57a6adffc6336fc572a4ff1f5dfc284ee4fb"),
    "prog.bin": (2097152, "4ce454e44da08e351a81ca4b670ff3e080dcb330"),
    "0l.bin": (4194304, "968de0cd275784cf082df172b7f205861a8fbae4"),
    "0h.bin": (4194304, "8e4a026f20b7ba035eb78713d64bfcb611c90640"),
    "1l.bin": (4194304, "c0fe45d7618653b089ec293e7661da682b77534d"),
    "1h.bin": (4194304, "4d589545765f55aaddb05f484fbf6248af3e237b"),
    "2l.bin": (4194304, "0722d89865cc2c9acda899bacb2787481c16c01a"),
    "2h.bin": (4194304, "c70f4b0ff3997bf94a89cd613a2877f062014393"),
    "3l.bin": (4194304, "be96626f3a4c8eb81f0bb7d8ac1c4e6619be50c8"),
    "3h.bin": (4194304, "9a8ddda4c6c007bb5cd4abb11859a4b7f1b1d578"),
    "snd0.bin": (4194304, "d229753b536209fe0da1985ca694fd1a73bc0f39"),
    "snd1.bin": (4194304, "2100d5d7d2e4b9ed90bde38cb61a5da09f00ce21"),
}


def load(path) -> dict[str, bytes]:
    """Load every profile file from a zip or directory and verify size and SHA1."""
    path = pathlib.Path(path)
    if path.is_dir():
        read = lambda n: (path / n).read_bytes() if (path / n).is_file() else None
    elif zipfile.is_zipfile(path):
        z = zipfile.ZipFile(path)
        names = set(z.namelist())
        read = lambda n: z.read(n) if n in names else None
    else:
        raise SourceError(f"source not found or not a zip/directory: {path}")
    files, errors = {}, []
    for name, (size, sha1) in PROFILE.items():
        data = read(name)
        if data is None:
            errors.append(f"{name}: missing")
        elif len(data) != size or hashlib.sha1(data).hexdigest() != sha1:
            errors.append(f"{name}: size/SHA1 mismatch")
        else:
            files[name] = data
    if errors:
        raise SourceError("unsupported source: " + "; ".join(errors))
    return files
