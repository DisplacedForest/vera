a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=[],
    datas=[
        ("SOUL.md", "."),
        ("HEARTBEAT.md", "."),
        ("BUILDER.md", "."),
        ("routers/leak_patterns.txt", "routers"),
        ("../../VERSION", "."),
    ],
    hiddenimports=[
        "uvicorn.logging",
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "uvicorn.loops.asyncio",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.http.h11_impl",
        "uvicorn.protocols.http.httptools_impl",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="vera-api",
    console=True,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="vera-api",
)
