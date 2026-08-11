"""
Dropbox API wrapper: authenticates via a long-lived refresh token and lists
folder contents scoped to the team space root namespace.

Business/team Dropbox accounts split "home" (personal) and "root" (team
space) namespaces. The shared ticker folders (Shared/Active, Shared/Inactive,
etc.) live in the team space, not the personal home folder, so every call
here is scoped to the root namespace rather than the SDK's default.
"""

import hashlib

import dropbox

from app.config import settings

# Cached Dropbox client, created once and reused across requests within
# this process rather than reconnecting every call (see get_client()).
_client: dropbox.Dropbox | None = None

# Cached Dropbox Business team ID the service account belongs to — see
# get_team_id().
_team_id: str | None = None

# System/junk files that show up in real folder listings but should never
# be treated as real research files or tickers (see list_folder()).
_JUNK_NAMES = {".ds_store"}  # macOS folder-metadata file
_JUNK_SUFFIXES = (".lnk",)  # Windows shortcut files


def get_client() -> dropbox.Dropbox:
    """
    Return an authenticated Dropbox client, creating (and caching) one on
    first use.

    Two things happen here that are easy to miss:
    1. Auth uses a long-lived refresh token (not a short-lived access
       token), so the app never needs a human to log in again — see
       README.md "Credential rotation" for how that token was obtained.
    2. The client is explicitly re-scoped to the Dropbox team's *root*
       namespace via `with_path_root`. Without this, every call would
       operate on the connecting account's personal "home" folder, which
       is empty — the real Shared/Active etc. folders live in the team
       space instead. This tripped us up once already; see the
       README's "Testing against sandbox data vs. real data" section.
    """
    global _client
    if _client is not None:
        return _client

    base_client = dropbox.Dropbox(
        oauth2_refresh_token=settings.dropbox_refresh_token,
        app_key=settings.dropbox_app_key,
        app_secret=settings.dropbox_app_secret,
    )
    root_namespace_id = base_client.users_get_current_account().root_info.root_namespace_id
    _client = base_client.with_path_root(dropbox.common.PathRoot.root(root_namespace_id))
    return _client


def get_team_id() -> str:
    """
    The Dropbox Business team ID the service account (and therefore every
    real Your Firm research folder) belongs to. Used by app/services/
    auth.py to verify a per-user sign-in is actually someone on that same
    team, not just any Dropbox account — access is exactly "whoever has
    already been given a real Dropbox seat on this team," no separate
    allow-list to maintain here. Cached the same way get_client() is.
    """
    global _team_id
    if _team_id is None:
        account = get_client().users_get_current_account()
        _team_id = account.team.id
    return _team_id


def _is_junk(name: str) -> bool:
    """True for system/shortcut files that should be invisible to the app
    (never shown as a ticker, file, or search result)."""
    return name.lower() in _JUNK_NAMES or name.lower().endswith(_JUNK_SUFFIXES)


def list_folder(path: str) -> list[dict]:
    """List the immediate contents of a Dropbox folder (not recursive —
    subfolders show up as single entries, their contents aren't expanded),
    filtering out system/junk files (.DS_Store, .lnk shortcuts).

    Dropbox returns large folders one page at a time; `has_more` /
    `files_list_folder_continue` below is the pagination loop that keeps
    fetching pages until there's nothing left, so callers always get the
    complete list regardless of folder size.

    A folder that doesn't exist yet is treated the same as an empty
    folder, rather than raising — this matters a lot in practice, since
    checking "does this ticker already have a file named X?" (see
    files.py's _resolve_upload) routinely happens against ticker folders
    that don't exist yet (a brand-new ticker's folder is only created by
    the upload itself). Without this, every brand-new-ticker upload would
    crash before it even got the chance to create the folder."""
    client = get_client()
    try:
        result = client.files_list_folder(path)
    except dropbox.exceptions.ApiError as error:
        if error.error.is_path() and error.error.get_path().is_not_found():
            return []
        raise
    entries = list(result.entries)
    while result.has_more:
        result = client.files_list_folder_continue(result.cursor)
        entries.extend(result.entries)

    return [
        {
            "name": entry.name,
            "is_folder": isinstance(entry, dropbox.files.FolderMetadata),
            # Only files have a content hash; folders don't. Lets callers
            # detect "this is the exact same file, not just a matching
            # name" without downloading and comparing the actual bytes —
            # see compute_content_hash() below.
            "content_hash": getattr(entry, "content_hash", None),
        }
        for entry in entries
        if not _is_junk(entry.name)
    ]


def list_folder_recursive(path: str) -> dict:
    """
    List every FILE inside a Dropbox folder, at any depth, filtering out
    system/junk files the same way list_folder() does. Unlike
    list_folder(), subfolder contents are expanded rather than shown as a
    single opaque folder entry — each file comes back with a
    `relative_path` giving its subfolder path within `path` ("" for a
    file directly in `path` itself, "Old Models" for one inside an
    "Old Models" subfolder, etc.) — same convention as the upload
    endpoint's `relative_path` (see files.py's upload_file()), so a
    folder uploaded with subfolders preserved can be displayed the same
    way it was uploaded.

    Returns `{"files": [...], "folders": [...]}` — `folders` is the
    relative_path of every real subfolder at any depth, INCLUDING ones
    with nothing inside them at all. A subfolder that already has a file
    somewhere inside it is already implicitly discoverable from that
    file's own `relative_path`, but a genuinely empty one (e.g. just
    created via the app's own "+ New folder" button, or in Dropbox
    directly) has no file to be inferred from — this is the only way the
    frontend can know it exists at all. Same not-found-is-empty and
    pagination behavior as list_folder().
    """
    client = get_client()
    try:
        result = client.files_list_folder(path, recursive=True)
    except dropbox.exceptions.ApiError as error:
        if error.error.is_path() and error.error.get_path().is_not_found():
            return {"files": [], "folders": []}
        raise
    entries = list(result.entries)
    while result.has_more:
        result = client.files_list_folder_continue(result.cursor)
        entries.extend(result.entries)

    base_prefix = f"{path.rstrip('/')}/"
    files = []
    folders = []
    for entry in entries:
        if _is_junk(entry.name):
            continue
        if isinstance(entry, dropbox.files.FolderMetadata):
            # path_display here is the folder's OWN full path (unlike a
            # file, where the portion after stripping the filename gives
            # its *containing* folder) — strip the base prefix directly.
            if entry.path_display.startswith(base_prefix):
                folders.append(entry.path_display[len(base_prefix) :])
            continue
        if not isinstance(entry, dropbox.files.FileMetadata):
            continue
        # path_display carries the file's full path from the root; the
        # portion between the base folder and the file's own name is its
        # subfolder path within `path`.
        full_dir = entry.path_display.rsplit("/", 1)[0]
        relative_path = full_dir[len(base_prefix) :] if full_dir.startswith(base_prefix) else ""
        # size/modified come straight off the same files_list_folder
        # response already being read here — no extra API call needed to
        # support sorting by them (see files.py's list_files_for_ticker()).
        files.append(
            {
                "name": entry.name,
                "relative_path": relative_path,
                "content_hash": entry.content_hash,
                "size": entry.size,
                "modified": entry.server_modified.isoformat(),
            }
        )

    return {"files": files, "folders": folders}


def compute_content_hash(content: bytes) -> str:
    """
    Compute Dropbox's own content-hash algorithm for a local byte string,
    so it can be compared directly against a real file's stored
    `content_hash` (from list_folder()) to check "is this literally the
    same content?" without downloading and byte-comparing the existing
    file.

    Algorithm (documented at
    https://www.dropbox.com/developers/reference/content-hash): split the
    content into 4MB blocks, SHA-256 each block, concatenate those hashes
    in order, then SHA-256 the concatenation. Any two files with identical
    content always produce the same hash, and Dropbox computes this same
    hash server-side for every file it stores.
    """
    block_size = 4 * 1024 * 1024
    block_hashes = b""
    for offset in range(0, len(content), block_size):
        block_hashes += hashlib.sha256(content[offset : offset + block_size]).digest()
    return hashlib.sha256(block_hashes).hexdigest()


def download(path: str) -> bytes:
    """Download a file's raw content from Dropbox — used by
    app/services/thumbnails.py for an actual .pdf file (no conversion
    needed, unlike the Word/PowerPoint-family types that go through
    get_preview() below)."""
    client = get_client()
    _, response = client.files_download(path)
    return response.content


def get_preview(path: str) -> bytes:
    """
    Dropbox's own rendered preview of a document — a PDF for Word/
    PowerPoint-family files (see thumbnails.py's extension list), HTML for
    spreadsheets (not used here — see thumbnails.py for why Excel/CSV
    don't get a real thumbnail). Different from get_image_thumbnail()
    below, which only works on files that are already actual images.
    """
    client = get_client()
    _, response = client.files_get_preview(path)
    return response.content


def get_image_thumbnail(path: str) -> bytes | None:
    """
    A small JPEG thumbnail for a file that's already an actual image
    (jpg/png/gif/etc — see dropbox.files.ThumbnailV2Arg's supported
    formats). Returns None rather than raising if Dropbox can't produce
    one (wrong type, corrupt, too large) — a missing thumbnail is an
    expected, normal outcome here, not an error condition."""
    client = get_client()
    try:
        _, response = client.files_get_thumbnail_v2(
            dropbox.files.PathOrLink.path(path),
            format=dropbox.files.ThumbnailFormat.jpeg,
            size=dropbox.files.ThumbnailSize.w256h256,
        )
        return response.content
    except dropbox.exceptions.ApiError:
        return None


def get_shareable_link(path: str) -> str:
    """
    A Dropbox web URL that opens `path` in Dropbox's own preview UI — used
    for "open in Dropbox" (see files.py). Reuses an existing shared link
    for this exact file if one's already been created (by a previous
    click here, or manually in Dropbox itself) rather than creating a new
    one on every request — `direct_only=True` so this only matches a link
    on the file itself, not one inherited from a parent folder.
    """
    client = get_client()
    existing = client.sharing_list_shared_links(path=path, direct_only=True)
    if existing.links:
        return existing.links[0].url
    link = client.sharing_create_shared_link_with_settings(path)
    return link.url


def upload_file(path: str, content: bytes, overwrite: bool = False) -> str:
    """Upload file content to a Dropbox path, creating any missing parent
    folders automatically. Returns the actual filename used (may differ
    from the requested one if a conflict caused an auto-rename).

    - overwrite=False (default): if a file already exists at this exact
      path, auto-renames instead of clobbering it. Callers that care about
      conflicts should check for one first (see files.py's duplicate-
      confirmation flow) — this default just guarantees the call never
      crashes or silently destroys data if a conflict slips through.
    - overwrite=True: replaces the existing file's content in place."""
    client = get_client()
    if overwrite:
        metadata = client.files_upload(content, path, mode=dropbox.files.WriteMode("overwrite"))
    else:
        metadata = client.files_upload(content, path, mode=dropbox.files.WriteMode("add"), autorename=True)
    return metadata.name


def create_folder(path: str) -> None:
    """
    Create an empty folder at `path`. Every other folder in this app gets
    created implicitly, as a side effect of uploading a file into it
    (`files_upload` auto-creates missing parent folders) — this is the one
    exception, for the case where someone drags in a folder with no files
    inside it at all and still wants the corresponding ticker folder to
    exist (see tickers.py's `/tickers/create`)."""
    client = get_client()
    client.files_create_folder_v2(path)


def move(from_path: str, to_path: str) -> None:
    """Move/rename a file or folder within Dropbox. Auto-renames on a
    naming conflict at the destination rather than failing outright."""
    client = get_client()
    client.files_move_v2(from_path, to_path, autorename=True)


def delete(path: str) -> None:
    """
    Permanently delete a file OR a whole folder (and everything inside it)
    from Dropbox. There's nothing folder-specific about this call — the
    same Dropbox API endpoint handles both, so one function covers
    deleting a single file and deleting an entire ticker.

    Not instantly, truly unrecoverable: Dropbox keeps its own trash and
    version history for a retention window, so a deleted file/folder can
    still be restored from Dropbox itself for a while after this call —
    but the app should still treat this as a real, deliberate action and
    always confirm with the user first (see the frontend's inline confirm
    dialogs before any delete button actually calls this).
    """
    client = get_client()
    client.files_delete_v2(path)


def search_files(query: str, max_results: int = 100) -> list[dict]:
    """Search filenames across the whole account for a query string. Uses
    Dropbox's own search rather than listing every subfolder ourselves,
    since that wouldn't scale to accounts with hundreds of ticker folders.

    Deliberately doesn't use SearchOptions' `path` scoping — testing showed
    it doesn't reliably restrict results when combined with a team-space
    path root (a scoped search returned matches from outside the given
    path). Callers should filter/derive meaning from each match's full
    path themselves instead of trusting scope from this function."""
    client = get_client()
    options = dropbox.files.SearchOptions(filename_only=True, max_results=max_results)
    result = client.files_search_v2(query, options=options)
    matches = []
    for match in result.matches:
        metadata = match.metadata.get_metadata()
        if isinstance(metadata, dropbox.files.FileMetadata) and not _is_junk(metadata.name):
            matches.append({"name": metadata.name, "path": metadata.path_display})
    return matches
