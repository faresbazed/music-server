"""Validate the IPA's device Mach-O headers and packaging without Xcode."""
import plistlib
import struct
import sys
import zipfile

APP = "Payload/OfflineMusic.app/"
with zipfile.ZipFile(sys.argv[1]) as z:
    assert z.testzip() is None
    info = plistlib.loads(z.read(APP + "Info.plist"))
    assert info["MinimumOSVersion"] == "9.0" and info["UIDeviceFamily"] == [2]
    assert info["CFBundleSupportedPlatforms"] == ["iPhoneOS"]
    assert not info.get("NSAppTransportSecurity")
    exe = z.getinfo(APP + info["CFBundleExecutable"])
    assert (exe.external_attr >> 16) & 0o111
    binary = z.read(exe)
    assert z.read(APP + "Icon-76.png").startswith(b"\x89PNG")

magic, narch = struct.unpack_from(">II", binary)
assert magic == 0xCAFEBABE and narch == 2
arches = set()
for i in range(narch):
    cpu, subtype, start, size, _ = struct.unpack_from(">IIIII", binary, 8 + i * 20)
    arches.add((cpu, subtype & 0x00FFFFFF))
    slice_ = binary[start:start + size]
    magic, c, _, filetype, ncmds, _, _ = struct.unpack_from("<7I", slice_)
    assert c == cpu and filetype == 2 and magic in {0xFEEDFACE, 0xFEEDFACF}
    pos = 32 if magic == 0xFEEDFACF else 28
    minos = signed = False
    for _ in range(ncmds):
        cmd, length = struct.unpack_from("<II", slice_, pos)
        assert length >= 8 and pos + length <= len(slice_)
        if cmd == 0x25:  # LC_VERSION_MIN_IPHONEOS
            version, = struct.unpack_from("<I", slice_, pos + 8)
            minos = version == 0x90000
        if cmd == 0x1D:  # LC_CODE_SIGNATURE
            off, siglen = struct.unpack_from("<II", slice_, pos + 8)
            signed = siglen > 0 and off + siglen <= len(slice_)
        pos += length
    assert minos and signed
assert arches == {(12, 9), (0x0100000C, 0)}
print("PASS: iPad bundle, armv7 + arm64 device executable, iOS 9.0 minimum, signature slots")
