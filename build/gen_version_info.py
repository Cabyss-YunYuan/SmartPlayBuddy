"""Generate version_info.txt from locale JSON."""
import json
import sys
import os

locales_dir = sys.argv[1]
output_file = sys.argv[2]

with open(os.path.join(locales_dir, 'en_US.json'), 'r', encoding='utf-8') as f:
    app = json.load(f)['app']

v = app['version'].split('.')
while len(v) < 4:
    v.append('0')
v = tuple(int(x) for x in v[:4])

content = f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={v},
    prodvers={v},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        u'080404b0',
        [StringStruct(u'CompanyName', u'{app["publisher"]}'),
         StringStruct(u'FileDescription', u'{app["name"]}'),
         StringStruct(u'FileVersion', u'{app["version"]}.0'),
         StringStruct(u'InternalName', u'{app["name"]}'),
         StringStruct(u'LegalCopyright', u'Copyright (c) {app["publisher"]}'),
         StringStruct(u'OriginalFilename', u'{app["exe_name"]}'),
         StringStruct(u'ProductName', u'{app["name"]}'),
         StringStruct(u'ProductVersion', u'{app["version"]}.0')])
    ]),
    VarFileInfo([VarStruct(u'Translation', [2052, 1200])])
  )
)
"""

with open(output_file, 'w', encoding='utf-8') as f:
    f.write(content)

print(f"  version_info.txt generated: {app['name']} v{app['version']}")
