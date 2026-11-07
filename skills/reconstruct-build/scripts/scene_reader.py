#!/usr/bin/env python3
"""Bounded queries over an MCP Scene export. Offline unless --base-url is given for signed asset downloads. Never executes capture data."""
import argparse
import hashlib
import html
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil
import subprocess
from urllib.parse import unquote, urlsplit
import urllib.request


def fact(value):
    return value.get('value') if isinstance(value, dict) and 'value' in value else None


def select(document, frame_id=None):
    if document.get('schema') != 'reconstruct.scene-export.v1':
        raise ValueError('Expected the MCP scene-export envelope, not raw HTML or a page dump.')
    scene = document['scene']
    if scene.get('schema') != 'reconstruct.scene.v1':
        raise ValueError('Unsupported Scene schema.')
    frame_id = frame_id or scene.get('entryFrameId')
    frames = [f for f in scene['frames'] if f['id'] == frame_id]
    if len(frames) != 1:
        raise ValueError('Select one exact frame ID; no implicit first-frame fallback.')
    frame = frames[0]
    nodes = {n['id']: n for n in frame['nodes']}
    if len(nodes) != len(frame['nodes']):
        raise ValueError('Duplicate node IDs within the selected frame.')
    return scene, frame, nodes


def visibility(node, nodes):
    """False if any ancestor is hidden; unknown stays unknown, never defaults true."""
    seen = set()
    unknown = False
    while node is not None:
        if node['id'] in seen:
            raise ValueError('Parent cycle in Scene.')
        seen.add(node['id'])
        value = fact(node.get('visible'))
        if value is False:
            return False
        unknown |= value is not True
        parent = node.get('parentId')
        if parent and parent not in nodes:
            raise ValueError('Missing parent in selected frame.')
        node = nodes.get(parent)
    return None if unknown else True


def row(node, nodes):
    semantics = node.get('semantics') or {}
    sensitive = semantics.get('sensitive', False)
    text = '' if sensitive else ''.join(run.get('text', '') for run in node.get('text', []))
    return {k: v for k, v in {
        'id': node['id'], 'parent': node.get('parentId'), 'kind': node['kind'],
        'role': semantics.get('role'), 'name': None if sensitive else semantics.get('name'),
        'selected': semantics.get('selected'), 'expanded': semantics.get('expanded'),
        'enabled': semantics.get('enabled'), 'modal': semantics.get('modal'),
        'visible': visibility(node, nodes), 'sensitive': True if sensitive else None,
        'text': text[:160] or None, 'textTruncated': True if len(text) > 160 else None,
        'bounds': fact((node.get('geometry') or {}).get('visualBounds')),
        'media': node.get('mediaResourceId'),
        'clip': fact((node.get('layout') or {}).get('clip')),
        'arrangement': fact((node.get('layout') or {}).get('arrangement')),
        'actions': node.get('actions') or None,
    }.items() if v is not None}


def page(items, offset, limit):
    return {'total': len(items), 'offset': offset, 'items': items[offset:offset+limit],
            'nextOffset': offset+limit if offset+limit < len(items) else None}


def overview(document, frame_id=None, offset=0, limit=40):
    scene, frame, nodes = select(document, frame_id)
    rows = [row(n, nodes) for n in nodes.values()]
    relevant = [r for r in rows if r.get('text') or r.get('name') or r.get('media') or
                r.get('actions') or r.get('role') not in (None, 'generic', 'none') or
                r.get('clip') == 'scroll']
    controls = [r for r in rows if r.get('actions') or r.get('role') in
                ('button', 'tab', 'link', 'textbox', 'checkbox', 'combobox', 'menuitem', 'dialog')]
    return {
        'schema': 'reconstruct.scene-reader.v1', 'sceneId': scene['id'],
        'note': 'Derived navigation index only. Values omit fact provenance here; nodes command returns exact canonical facts. Missing values are unknown. Controls/actions do not prove a working destination. Hidden nodes are not initial UI.',
        'frames': [{k: f.get(k) for k in ('id', 'profileId', 'stateId', 'truncated')} for f in scene['frames']],
        'selectedFrame': {k: v for k, v in frame.items() if k != 'nodes'},
        'profiles': scene['profiles'], 'nodeCount': len(nodes),
        'content': page(relevant, offset, limit), 'controls': page(controls, offset, limit),
        'scrollCandidates': page([r for r in rows if r.get('clip') == 'scroll'], offset, limit),
        'transitions': page(scene.get('transitions', []), offset, limit),
        'issues': page(scene.get('issues', []), offset, limit),
        'assets': {'archived': len(document.get('assets', {})),
                   'unavailable': [r['id'] for r in scene.get('resources', []) if r['id'] not in document.get('assets', {})]},
    }


def exact_nodes(document, ids, frame_id=None):
    _, frame, nodes = select(document, frame_id)
    missing = [identifier for identifier in ids if identifier not in nodes]
    if missing:
        raise ValueError('Unknown node IDs: ' + ', '.join(missing))
    if any((nodes[i].get('semantics') or {}).get('sensitive') for i in ids):
        raise ValueError('Use reviewed/redacted Scene evidence; sensitive nodes cannot be expanded.')
    return {'frameId': frame['id'], 'nodes': [nodes[i] for i in ids]}


def implementation_spec(document, frame_id=None, redact=False, table=None):
    """Lossless numeric values; provenance remains at explicit canonical pointers.
    redact=True keeps sensitive nodes as structure only (no name, no text runs).
    table=(styles, identities) shares one style table across several frames."""
    scene, frame, nodes = select(document, frame_id)
    frame_index = scene['frames'].index(frame)
    styles, identities = table if table is not None else ({}, {})

    def values(value):
        if isinstance(value, dict):
            if {'value', 'kind', 'evidence'} <= value.keys():
                return values(value['value'])
            return {key: values(item) for key, item in value.items()}
        if isinstance(value, list):
            return [values(item) for item in value]
        return value

    def style(value):
        value = values(value)
        key = json.dumps(value, sort_keys=True, separators=(',', ':'))
        if key not in identities:
            identifier = 's' + str(len(styles))
            identities[key] = identifier
            styles[identifier] = value
        return identities[key]

    result = []
    for index, node in enumerate(nodes.values()):
        sensitive = (node.get('semantics') or {}).get('sensitive')
        if sensitive and not redact:
            raise ValueError('Cannot prepare an implementation projection from sensitive nodes.')
        item = {key: node[key] for key in ('id', 'parentId', 'kind', 'semantics', 'mediaResourceId', 'actions', 'motion') if key in node}
        if sensitive:
            item['semantics'] = {key: value for key, value in node['semantics'].items() if key != 'name'}
        item['canonicalPointer'] = f'/scene/frames/{frame_index}/nodes/{index}'
        item['effectiveVisible'] = visibility(node, nodes)
        if 'geometry' in node: item['geometry'] = values(node['geometry'])
        for key in ('layout', 'paint'):
            if key in node: item[key + 'Style'] = style(node[key])
        item['text'] = [{**{key: value for key, value in run.items() if key != 'typography'},
                         **({'typographyStyle': style(run['typography'])} if 'typography' in run else {})}
                        for run in ([] if sensitive else node.get('text', []))]
        result.append(item)
    return {'schema': 'reconstruct.implementation-index.v1',
            'note': 'Derived values-only projection for implementation, not canonical evidence. Every node points into the original export; read exact facts there to distinguish measured, declared and inferred values. Absent fields and null visibility remain unknown. No layout rules or successful interactions are invented.',
            'sceneId': scene['id'], 'frame': {key: value for key, value in frame.items() if key != 'nodes'},
            'profiles': scene['profiles'], 'nodes': result, 'styles': styles,
            'resources': scene.get('resources', []), 'assets': document.get('assets', {}),
            'transitions': scene.get('transitions', []), 'issues': scene.get('issues', [])}


def readable_brief(document, frame_id=None):
    scene, frame, nodes = select(document, frame_id)
    lines = ['# Selected Scene',
             'Navigation summary only; expand canonical facts for provenance. Unknown stays unknown.',
             f'Scene: {scene["id"]}; frame: {frame["id"]}; profile: {frame.get("profileId")}; state: {frame.get("stateId")}',
             'Frame: ' + json.dumps({k: frame.get(k) for k in ('coordinateSpace', 'extent', 'viewportBounds', 'truncated')}),
             'Other frames: ' + json.dumps([{k: f.get(k) for k in ('id', 'profileId', 'stateId')} for f in scene['frames'] if f is not frame]),
             'Transitions: ' + json.dumps(scene.get('transitions', [])),
             'Issues: ' + json.dumps(scene.get('issues', [])),
             '', '## Content and controls',
             'One line per meaningful node: ID, parent, visibility, role/state, bounds, text/media. Hidden nodes are not initial UI.']
    for node in nodes.values():
        item = row(node, nodes)
        if not (item.get('text') or item.get('name') or item.get('media') or item.get('actions') or
                item.get('role') not in (None, 'generic', 'none') or item.get('clip') == 'scroll'):
            continue
        bounds = item.get('bounds') or {}
        bits = [item['id'], 'parent='+str(item.get('parent', '?')),
                'visible='+str(item.get('visible', 'unknown')),
                str(item.get('role') or item['kind']),
                'bounds='+','.join(str(bounds.get(key, '?')) for key in ('x', 'y', 'width', 'height'))]
        for key in ('selected', 'expanded', 'enabled', 'modal', 'clip', 'arrangement', 'media', 'actions'):
            if key in item: bits.append(key+'='+str(item[key]))
        if item.get('name'): bits.append('name='+json.dumps(item['name'], ensure_ascii=False))
        if item.get('text'): bits.append('text='+json.dumps(item['text'], ensure_ascii=False))
        lines.append(' | '.join(bits))
    lines += ['', '## Resources',
              f'Archived: {len(document.get("assets", {}))}; original resource declarations: {len(scene.get("resources", []))}.',
              'Use the verified assets map and capture-spec styles instead of rediscovering them. Missing font bytes require a new capture.']
    return '\n'.join(lines)+'\n'


def observed_transitions(scene, frame_id):
    """Measured outcomes from this frame; changed needs an exported destination frame."""
    frames = {frame['id'] for frame in scene['frames']}
    return [transition for transition in scene.get('transitions', [])
            if transition['fromFrameId'] == frame_id and transition.get('kind') == 'measured' and
            (transition.get('outcome') == 'unchanged' or
             (transition.get('outcome') == 'changed' and transition.get('toFrameId') in frames))]


def coverage(document):
    scene, _, _ = select(document)
    assets = document.get('assets', {})
    result = []
    for frame in scene['frames']:
        nodes = frame['nodes']
        actions = [(node['id'], action['kind']) for node in nodes for action in node.get('actions', [])]
        observed = {(transition.get('nodeId'), transition['action']) for transition in observed_transitions(scene, frame['id'])}
        media = [node for node in nodes if node.get('mediaResourceId')]
        containers = [node for node in nodes if node.get('kind') == 'container']
        result.append({
            'frameId': frame['id'], 'profileId': frame['profileId'], 'truncated': frame.get('truncated', False),
            'nodeCount': len(nodes), 'containerCount': len(containers),
            'containersWithArrangement': sum(fact((node.get('layout') or {}).get('arrangement')) not in (None, 'unknown') for node in containers),
            'nodesWithExplicitVisibility': sum(fact(node.get('visible')) is not None for node in nodes),
            'declaredActions': len(actions), 'actionsWithObservedOutcome': sum(action in observed for action in actions),
            'actionsWithoutObservedOutcome': [{'nodeId': node, 'action': action} for node, action in actions if (node, action) not in observed],
            'nodesWithStates': sum(bool(node.get('states')) for node in nodes),
            'measuredStates': sum(len(node.get('states') or []) for node in nodes),
            'mediaReferences': len(media),
            'unavailableMediaReferences': [{'nodeId': node['id'], 'resourceId': node['mediaResourceId']} for node in media if node['mediaResourceId'] not in assets],
        })
    return {'schema': 'reconstruct.scene-coverage.v1', 'sceneId': scene['id'], 'platform': scene['platform'],
            'note': 'Evidence inventory, not a fidelity score or proof of working implementation. A hover transition does not verify click; declared/failed/unverified actions are not observed successful outcomes. Missing data needs capture/adapter/format work, never application source recovery.',
            'frames': result, 'archivedResources': len(assets), 'declaredResources': len(scene.get('resources', [])),
            'fontResources': sum(resource.get('kind') == 'font' and resource['id'] in assets for resource in scene.get('resources', [])),
            'themes': [{'id': theme['id'], 'current': theme.get('current', False), 'tokens': len(theme.get('tokens') or {})}
                       for theme in scene.get('themes') or []],
            'layoutSamples': [{'id': sample['id'], 'width': sample['width'], 'frameId': sample['frameId'],
                               'nodes': len(sample.get('nodes') or [])} for sample in scene.get('layoutSamples') or []],
            'issues': scene.get('issues', [])}


def _download(base_url, uri, limit=64 * 1024 * 1024):
    """GET base_url + uri for a relative /mcp-transfers/ or /mcp-assets/ artifact URI; no credentials are sent."""
    if not isinstance(uri, str) or not re.match(r'^/mcp-(transfers|assets)/[A-Za-z0-9._-]+$', uri):
        raise ValueError('Not a relative MCP artifact URI: ' + str(uri))
    request = urllib.request.Request(base_url.rstrip('/') + uri, headers={'User-Agent': 'reconstruct-scene-reader/1.0'})
    with urllib.request.urlopen(request, timeout=60) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError('Artifact exceeds the download limit: ' + uri)
    return data


def copy_assets(document, output, base_url=None):
    """Server-local files, or with base_url (download.baseUrl from the MCP tool result) signed HTTPS downloads.
    Every byte is checked against the export's SHA256."""
    if base_url is not None:
        parts = urlsplit(base_url)
        if parts.scheme != 'https' and parts.hostname not in ('localhost', '127.0.0.1'):
            raise ValueError('--base-url must be the https download.baseUrl returned by the MCP tool.')
    output.mkdir(parents=True, exist_ok=True)
    result = {}
    suffixes = {'image/png': '.png', 'image/jpeg': '.jpg', 'image/webp': '.webp',
                'image/svg+xml': '.svg', 'image/gif': '.gif', 'font/woff2': '.woff2',
                'font/woff': '.woff', 'font/ttf': '.ttf', 'font/otf': '.otf', 'image/avif': '.avif',
                'video/mp4': '.mp4', 'video/webm': '.webm', 'audio/mpeg': '.mp3', 'audio/ogg': '.ogg'}
    for identifier, asset in document.get('assets', {}).items():
        source = Path(asset['path']) if asset.get('path') else None
        data = None
        if source is None or not source.is_file():
            if base_url is None or not asset.get('uri'):
                result[identifier] = {'available': False, 'reason': 'Server path unavailable locally; rerun assets/scaffold with '
                                      '--base-url set to download.baseUrl from the MCP tool result.'}
                continue
            try:
                data = _download(base_url, asset['uri'])
            except (OSError, ValueError) as error:
                result[identifier] = {'available': False, 'reason': 'Download failed: ' + str(error)[:200]}
                continue
        digest = hashlib.sha256(data if data is not None else source.read_bytes()).hexdigest()
        if digest != asset.get('sha256'):
            raise ValueError('Asset SHA256 mismatch: ' + identifier)
        suffix = suffixes.get((asset.get('mimeType') or '').split(';')[0], '.bin')
        destination = output / (digest + suffix)
        if destination.is_symlink():
            raise ValueError('Refusing a symlink output.')
        if data is not None:
            destination.write_bytes(data)
        elif source.resolve() != destination.resolve():
            shutil.copyfile(source, destination)
        result[identifier] = {'available': True, 'file': str(destination), 'sha256': digest}
        rendered = symbol_render_copy(destination) if suffix == '.svg' else None
        if rendered:
            result[identifier].update({'file': str(rendered), 'renderedFrom': str(destination)})
    return result


def symbol_render_copy(path):
    """Older captures stored sprite icons as <svg><symbol>…</symbol></svg> without <use>, which renders blank.
    Writes a derived <sha>.render.svg (symbol -> g, viewBox on the root); the original verified file is kept."""
    text = path.read_text(encoding='utf-8', errors='strict')
    if '<symbol' not in text or re.search(r'<use\b', text):
        return None
    root = re.match(r'\s*<svg\b[^>]*>', text)
    symbol = re.search(r'<symbol\b([^>]*)>', text)
    if not root or not symbol:
        return None
    head = root.group(0)
    view_box = re.search(r'viewBox="([^"]+)"', symbol.group(1))
    if view_box and 'viewBox=' not in head:
        head = head[:-1] + ' viewBox="' + view_box.group(1) + '">'
    body = re.sub(r'<symbol\b([^>]*)>', lambda match: '<g' + re.sub(r'\s(?:viewBox|id)="[^"]*"', '', match.group(1)) + '>',
                  text[root.end():]).replace('</symbol>', '</g>')
    rendered = path.with_name(path.stem + '.render.svg')
    rendered.write_text(head + body, encoding='utf-8', newline='\n')
    return rendered



ARIA_ROLES = {'alert', 'button', 'checkbox', 'dialog', 'grid', 'heading', 'img', 'link', 'list', 'listbox', 'listitem',
              'menu', 'menubar', 'menuitem', 'navigation', 'banner', 'contentinfo', 'main', 'option', 'progressbar',
              'radio', 'radiogroup', 'region', 'search', 'searchbox', 'slider', 'switch', 'tab', 'tablist', 'tabpanel',
              'textbox', 'toolbar', 'tooltip', 'combobox', 'status', 'separator', 'group', 'table', 'row', 'cell'}
NATIVE_ROLES = {'button': 'button', 'imagebutton': 'button', 'imageview': 'img', 'edittext': 'textbox',
                'autocompletetextview': 'combobox', 'checkbox': 'checkbox', 'switch': 'switch', 'togglebutton': 'switch',
                'radiobutton': 'radio', 'seekbar': 'slider', 'progressbar': 'progressbar', 'spinner': 'combobox',
                'tabwidget': 'tablist', 'image': 'img'}
ROLE_TAGS = {'button': 'button', 'link': 'a', 'textbox': 'input', 'searchbox': 'input', 'checkbox': 'input',
             'img': 'img', 'list': 'ul', 'listitem': 'li', 'navigation': 'nav', 'banner': 'header',
             'contentinfo': 'footer', 'heading': 'h2', 'dialog': 'section', 'menuitem': 'button', 'tab': 'button',
             'option': 'button'}
IMPLICIT_ROLES = {'button': 'button', 'a': 'link', 'ul': 'list', 'li': 'listitem', 'nav': 'navigation', 'header': 'banner',
                  'footer': 'contentinfo', 'img': 'img', 'h2': 'heading'}
VOID_TAGS = {'img', 'input', 'video'}
EVENTS = {'activate': 'click', 'navigate': 'click', 'back': 'click', 'hover': 'mouseenter', 'focus': 'focus', 'edit': 'input'}
ALIGN = {'start': 'flex-start', 'center': 'center', 'end': 'flex-end', 'stretch': 'stretch', 'baseline': 'baseline',
         'spaceBetween': 'space-between', 'spaceAround': 'space-around', 'spaceEvenly': 'space-evenly'}
GENERIC_FONTS = {'serif', 'sans-serif', 'monospace', 'cursive', 'fantasy', 'system-ui', 'ui-sans-serif', 'ui-serif',
                 'ui-monospace', '-apple-system', 'blinkmacsystemfont', 'roboto', 'sans-serif-medium'}
BASE_CSS = ('/* Generated by scene_reader.py scaffold. Classes sN are the capture-spec style table; '
            '[data-node-id] rules hold per-node placement. */\n'
            '[hidden]{display:none!important}\n'
            '*,*::before,*::after{box-sizing:border-box}\n'
            'html,body{margin:0;padding:0}\n'
            'p,h2,ul,li,figure{margin:0;padding:0;list-style:none}\n'
            'button,input{font:inherit;color:inherit;background:none;border:0;padding:0;margin:0;text-align:inherit}\n'
            'img,video{display:block}\n'
            '[data-arrangement="stack"]>*{grid-area:1/1}\n'
            '[data-media-of]{width:100%;height:100%}\n')


def _n(value):
    return ('%.4f' % value).rstrip('0').rstrip('.') if isinstance(value, float) else str(value)


def _px(value):
    return '0' if value == 0 else _n(value) + 'px'


def _rgba(color):
    channels = [_n(round(color[key] * 255, 2)) for key in ('red', 'green', 'blue')]
    return 'rgba(%s,%s)' % (','.join(channels), _n(round(color.get('alpha', 1), 4)))


def _css_string(value):
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('\n', ' ') + '"'


STATE_SELECTORS = {'hover': ':hover', 'focus': ':focus', 'focus-visible': ':focus-visible', 'focus-within': ':focus-within',
                   'active': ':active', 'disabled': ':is(:disabled,[aria-disabled="true"])', 'checked': ':checked'}
_CSS_PROPERTY = re.compile(r'^-?[a-z][a-z0-9-]*$')
_CSS_TOKEN = re.compile(r'^--[A-Za-z0-9_-]{1,120}$')


def _kebab(name):
    return re.sub(r'([A-Z])', lambda match: '-' + match.group(1).lower(), name)


def _safe_css_value(value):
    """Captured values are untrusted: keep plain values only, never anything that can close a rule or fetch."""
    if not isinstance(value, str) or not value.strip() or len(value) > 400:
        return None
    lowered = value.lower()
    if any(char in value for char in '{};<>\\\n\r') or 'url(' in lowered or 'expression(' in lowered or '@import' in lowered:
        return None
    return value.strip()


STROKE_STYLES = {'solid', 'dashed', 'dotted', 'double'}
_TOKEN_NAME = re.compile(r'^[A-Za-z0-9_-]{1,120}$')
_AREA_NAME = re.compile(r'^[A-Za-z_][A-Za-z0-9_-]{0,120}$')
# CSS families a neutral state patch already covers; other source properties come from extensions.web.
PATCH_FAMILIES = ('background', 'opacity', 'border', 'outline', 'box-shadow', 'color', 'text-decoration', 'font-size',
                  'font-weight', 'transform', 'display', 'visibility')


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _shadows_css(shadows):
    return ','.join(('inset ' if shadow.get('inset') else '') + ' '.join(_px(shadow[key]) for key in ('x', 'y', 'blur', 'spread'))
                    + ' ' + _rgba(shadow['color']) for shadow in shadows) or 'none'


def _fraction(value, scale, unit):
    return _n(round(value * scale, 4)) + unit


def gradient_css(gradients):
    """background-image layers from neutral gradients (first is topmost); an empty list clears the image."""
    layers = []
    for gradient in gradients:
        kind = gradient.get('type')
        unit, scale = ('deg', 360) if kind == 'conic' else ('%', 100)
        stops = ','.join(_rgba(stop['color']) + ('' if not _number(stop.get('position')) else ' ' + _fraction(stop['position'], scale, unit))
                         for stop in gradient.get('stops') or [])
        center = gradient.get('center')
        at = ' at %s %s' % (_fraction(center['x'], 100, '%'), _fraction(center['y'], 100, '%')) if center else ''
        angle = gradient.get('angle')
        if kind == 'linear':
            head = _n(angle if _number(angle) else 180) + 'deg'
        elif kind == 'radial':
            head = ('circle' if gradient.get('circle') else 'ellipse') + at
        elif kind == 'conic':
            head = 'from ' + _n(angle if _number(angle) else 0) + 'deg' + at
        else:
            continue
        layers.append(('repeating-' if gradient.get('repeating') else '') + kind + '-gradient(' + head + ',' + stops + ')')
    return ','.join(layers) or 'none'


def stroke_css(strokes, reset=False):
    """Inside strokes become borders, outside strokes the outline; reset clears both first (a state patch replaces the list)."""
    out = ['border-style:none', 'outline:none'] if reset else []
    for stroke in strokes:
        style = stroke.get('style') if stroke.get('style') in STROKE_STYLES else 'solid'
        line = _px(stroke['width']) + ' ' + style + ' ' + _rgba(stroke['color'])
        if stroke.get('placement') == 'outside':
            out.append('outline:' + line)
            if _number(stroke.get('offset')): out.append('outline-offset:' + _px(stroke['offset']))
        else:
            out += ['border-' + edge + ':' + line for edge in stroke['edges']] if stroke.get('edges') else ['border:' + line]
    return out


def patch_css(patch):
    """CSS declarations for a neutral state patch; present fields replace the node's values."""
    out = []
    if patch.get('fill'): out.append('background-color:' + _rgba(patch['fill']))
    if isinstance(patch.get('gradients'), list): out.append('background-image:' + gradient_css(patch['gradients']))
    if _number(patch.get('opacity')): out.append('opacity:' + _n(patch['opacity']))
    if patch.get('cornerRadii'):
        out.append('border-radius:' + ' '.join(_px(patch['cornerRadii'][side]) for side in ('top', 'right', 'bottom', 'left')))
    if isinstance(patch.get('strokes'), list): out += stroke_css(patch['strokes'], reset=True)
    if isinstance(patch.get('shadows'), list): out.append('box-shadow:' + _shadows_css(patch['shadows']))
    if patch.get('textColor'): out.append('color:' + _rgba(patch['textColor']))
    if isinstance(patch.get('decorations'), list):
        out.append('text-decoration-line:' + (' '.join({'strikethrough': 'line-through'}.get(item, item) for item in patch['decorations']) or 'none'))
    if _number(patch.get('fontSize')): out.append('font-size:' + _px(patch['fontSize']))
    if _number(patch.get('fontWeight')): out.append('font-weight:' + _n(patch['fontWeight']))
    transform = patch.get('transform')
    if isinstance(transform, list) and len(transform) == 16 and all(_number(value) for value in transform):
        out.append('transform:matrix3d(' + ','.join(_n(float(value)) for value in transform) + ')')
    if patch.get('visible') is False: out.append('display:none')
    elif patch.get('visible') is True: out.append('visibility:visible')
    return out


def _web_extension(item):
    return ((item or {}).get('extensions') or {}).get('web') or {}


def state_rules(raw_nodes, emitted):
    """CSS for measured interaction states (nodes[].states); unknown states and unsafe values are reported, not guessed."""
    rules, skipped = [], []
    for node_id in sorted(emitted):
        for state in (raw_nodes.get(node_id) or {}).get('states') or []:
            pseudo = STATE_SELECTORS.get(state.get('state'))
            declarations = patch_css(state.get('patch') or {})
            # This is the Web output adapter, so Web source properties without a neutral field (cursor...) are reused.
            for name, value in sorted(_web_extension(state).items()):
                prop, safe = _kebab(name), _safe_css_value(value)
                covered = any(prop == family or prop.startswith(family + '-') for family in PATCH_FAMILIES)
                if not covered and safe is not None and _CSS_PROPERTY.match(prop):
                    declarations.append(prop + ':' + safe)
            trigger = state.get('triggerNodeId')
            if pseudo is None or not declarations or (trigger and trigger not in emitted):
                skipped.append({'nodeId': node_id, 'state': state.get('state'),
                                'reason': 'no CSS selector for this state' if pseudo is None else
                                'trigger node not emitted' if declarations else 'no safe declarations'})
                continue
            target = '[data-node-id=' + _css_string(node_id) + ']'
            selector = ('[data-node-id=' + _css_string(trigger) + ']' + pseudo + ' ' + target) if trigger else target + pseudo
            rules.append(_rule(selector, declarations))
    return rules, skipped


def theme_rules(scene):
    """Current theme tokens on :root, other captured themes under [data-theme=ID] (no OS switching was measured)."""
    themes = scene.get('themes') or []
    current = next((theme for theme in themes if theme.get('current')), None)
    base = theme_tokens(current or {})
    rules, summary = [], []
    for theme in themes:
        tokens = theme_tokens(theme)
        changed = tokens if theme is current else {key: value for key, value in tokens.items() if base.get(key) != value}
        declarations = [key + ':' + value for key, value in sorted(changed.items())]
        selector = ':root' if theme is current else ':root[data-theme=' + _css_string(theme['id']) + ']'
        if declarations:
            rules.append(_rule(selector, declarations))
        summary.append({'id': theme['id'], 'current': bool(theme.get('current')), 'selector': selector,
                        'tokens': len(declarations), 'evidence': theme.get('evidence') or []})
    return rules, summary


def token_css(token):
    """CSS value of a typed theme token, or None."""
    kind = token.get('type')
    if kind == 'color' and token.get('color'): return _rgba(token['color'])
    if kind == 'dimension' and _number(token.get('number')): return _px(token['number'])
    if kind == 'number' and _number(token.get('number')): return _n(token['number'])
    if kind == 'shadow' and token.get('shadows'): return _shadows_css(token['shadows'])
    if kind == 'fontFamily' and token.get('fontFamilies'):
        return ','.join(family if family.lower() in GENERIC_FONTS else _css_string(family) for family in token['fontFamilies'])
    return None


def theme_tokens(theme):
    """Custom properties for one theme: typed tokens named --NAME, plus safe untyped Web source tokens."""
    tokens = {}
    for name, value in _web_extension(theme).items():
        if _CSS_TOKEN.match(name) and (safe := _safe_css_value(value)) is not None:
            tokens[name] = safe
    for token in theme.get('tokens') or []:
        name = token.get('name') if isinstance(token, dict) else None
        if isinstance(name, str) and _TOKEN_NAME.match(name) and (value := token_css(token)) is not None:
            tokens['--' + name] = value
    return tokens


def sample_css(entry):
    """CSS properties for the typed layout measured in a width sample."""
    if entry.get('visible') is False:
        return {'display': 'none'}
    css = {}
    arrangement = entry.get('arrangement')
    if arrangement in ('row', 'column'):
        css['display'] = 'flex'
        css['flex-direction'] = arrangement + ('-reverse' if entry.get('reverse') else '')
    elif arrangement == 'grid':
        css['display'] = 'grid'
        tracks = entry.get('columnTracks')
        if isinstance(tracks, list) and tracks and all(_number(track) for track in tracks):
            css['grid-template-columns'] = ' '.join(_px(track) for track in tracks)
    elif arrangement == 'flow':
        css['display'] = 'block'
    if _number(entry.get('fontSize')):
        css['font-size'] = _px(entry['fontSize'])
    return css


def layout_sample_table(scene, frame_id, emitted):
    """Per-node geometry at each sampled width, for writing media queries by hand. Sparse: only sampled nodes."""
    frames = {frame['id']: frame for frame in scene.get('frames') or []}

    def evidence_key(node):
        return next((item for item in node.get('evidence') or [] if re.match(r'^web:elements?[/:]\d+$', item)), None)

    target = {key: node['id'] for node in (frames.get(frame_id) or {}).get('nodes') or [] if (key := evidence_key(node))}
    translations = {}

    def translate(source_frame, node_id):
        # Samples are measured once against one frame; other frames of the same page share element evidence.
        if source_frame == frame_id:
            return node_id
        if source_frame not in translations:
            translations[source_frame] = {node['id']: target.get(evidence_key(node))
                                          for node in (frames.get(source_frame) or {}).get('nodes') or []}
        return translations[source_frame].get(node_id)

    samples = [sample for sample in scene.get('layoutSamples') or [] if sample.get('frameId') in frames]
    table = {}
    for sample in samples:
        key = _n(sample['width'])
        for item in sample.get('nodes') or []:
            node_id = translate(sample['frameId'], item['nodeId'])
            if node_id in emitted:
                bounds = item['bounds']
                table.setdefault(node_id, {})[key] = {'x': bounds['x'], 'y': bounds['y'], 'width': bounds['width'],
                                                      'height': bounds['height'], **(item.get('layout') or {})}
    unplaced = {_n(sample['width']): [{key: value for key, value in {
        'id': item.get('id'), 'parentNodeId': item.get('parentNodeId'), 'path': _web_extension(item).get('path'),
        **item['bounds'], **(item.get('layout') or {})}.items() if value is not None}
        for item in sample.get('unplaced') or []]
        for sample in samples if sample.get('unplaced')}
    summary = [{'id': sample['id'], 'width': sample['width'], 'height': sample['height'],
                'nodes': sum(translate(sample['frameId'], item['nodeId']) in emitted for item in sample.get('nodes') or []),
                'unplaced': len(sample.get('unplaced') or []),
                'evidence': sample.get('evidence') or []} for sample in sorted(samples, key=lambda item: item['width'])]
    return {'schema': 'reconstruct.layout-samples.v1', 'frameId': frame_id,
            'note': 'Measured geometry at other window widths keyed by node ID then width; breakpoints.css applies the '
                    'layout differences. Nodes absent here were not sampled. unplaced lists elements that exist only at that '
                    'width (nearest ancestor node, CSS path, bounds, layout) and have no node in this frame: build them for '
                    'that width. '
                    'Appearance and text were not re-measured.',
            'widths': summary, 'nodes': table, 'unplaced': unplaced}, summary


def breakpoint_css(table, widths, profile_width):
    """@media (max-width: W) blocks with the measured layout differences from the widest sample.
    A sample at W stands for widths up to W until the next narrower sample; the real breakpoint lies in between."""
    ordered = sorted((float(key) for key in {key for node in table.values() for key in node}), reverse=True)
    if len(ordered) < 2 and not (ordered and profile_width and ordered[0] < profile_width):
        return '', 0
    reference = _n(ordered[0])
    blocks, changed = [], 0
    for width in ordered[1:] if len(ordered) > 1 else ordered:
        key = _n(width)
        rules = []
        for node_id, samples in sorted(table.items()):
            here, base = samples.get(key), samples.get(reference)
            if here is None or (base is None and key != reference):
                continue
            here_css, base_css = sample_css(here), sample_css(base or {})
            declarations = [prop + ':' + value for prop, value in here_css.items() if base is None or base_css.get(prop) != value]
            if base is not None and abs(here['width'] - base['width']) > 1:
                declarations.append('width:min(' + _px(here['width']) + ',100%)')
            if declarations:
                rules.append(_rule('[data-node-id=' + _css_string(node_id) + ']', declarations))
        if rules:
            changed += len(rules)
            blocks.append('@media (max-width:%spx){\n#scene-root{width:100%%}\n%s}\n' % (key, ''.join(rules)))
    if not blocks:
        return '', 0
    header = ('/* Generated by scene_reader.py from scene layoutSamples: measured differences from the widest sample (%spx). '
              'Each block covers widths up to its sample; the original breakpoint lies between samples. */\n' % reference)
    return header + ''.join(blocks), changed


def _rule(selector, declarations):
    return selector + '{' + ';'.join(declarations) + '}\n' if declarations else ''


def normalized_role(semantics):
    role = (semantics or {}).get('role')
    if not role:
        return None
    lower = role.lower()
    if lower in ARIA_ROLES:
        return lower
    return NATIVE_ROLES.get(lower.split('.')[-1].split('$')[-1])


def style_css(style):
    """Context-free CSS for one capture-spec style entry (layout, paint or typography values)."""
    out = []
    arrangement = style.get('arrangement')
    if arrangement in ('row', 'column'):
        out += ['display:flex', 'flex-direction:' + arrangement + ('-reverse' if style.get('reverse') else '')]
        if style.get('wrap') is True: out.append('flex-wrap:wrap')
    elif arrangement in ('grid', 'stack'):
        out.append('display:grid')
        for key, prop in (('columnTracks', 'grid-template-columns'), ('rowTracks', 'grid-template-rows')):
            if style.get(key): out.append(prop + ':' + ' '.join(_px(track) for track in style[key]))
    if _number(style.get('grow')): out.append('flex-grow:' + _n(style['grow']))
    if _number(style.get('shrink')): out.append('flex-shrink:' + _n(style['shrink']))
    if _number(style.get('basis')): out.append('flex-basis:' + _px(style['basis']))
    if ALIGN.get(style.get('selfAlignment')): out.append('align-self:' + ALIGN[style['selfAlignment']])
    if isinstance(style.get('order'), int) and not isinstance(style.get('order'), bool): out.append('order:' + str(style['order']))
    cell = style.get('gridCell') or {}
    if isinstance(cell.get('area'), str) and _AREA_NAME.match(cell['area']):
        out.append('grid-area:' + cell['area'])
    else:
        for axis, prop in (('column', 'grid-column'), ('row', 'grid-row')):
            start, span = cell.get(axis), cell.get(axis + 'Span')
            parts = ([str(start)] if isinstance(start, int) else []) + (['span ' + str(span)] if isinstance(span, int) else [])
            if parts: out.append(prop + ':' + ' / '.join(parts))
    if style.get('rowGap') is not None: out.append('row-gap:' + _px(style['rowGap']))
    if style.get('columnGap') is not None: out.append('column-gap:' + _px(style['columnGap']))
    for key, prop in (('mainAlignment', 'justify-content'), ('crossAlignment', 'align-items')):
        value = ALIGN.get(style.get(key))
        if value and not (key == 'mainAlignment' and value == 'baseline'): out.append(prop + ':' + value)
    if style.get('padding'):
        out.append('padding:' + ' '.join(_px(style['padding'][side]) for side in ('top', 'right', 'bottom', 'left')))
    for axis, size in (('horizontal', 'width'), ('vertical', 'height')):
        constraint = style.get(axis) or {}
        if constraint.get('policy') == 'fixed' and constraint.get('value') is not None:
            out.append(size + ':' + _px(constraint['value']))
        if constraint.get('minimum') is not None: out.append('min-' + size + ':' + _px(constraint['minimum']))
        if constraint.get('maximum') is not None: out.append('max-' + size + ':' + _px(constraint['maximum']))
    if style.get('clip') == 'bounds': out.append('overflow:hidden')
    elif style.get('clip') == 'scroll': out.append('overflow:auto')
    placement = style.get('placement')
    if placement in ('overlay', 'anchored'): out.append('position:absolute')
    elif placement == 'sticky': out.append('position:sticky')
    if style.get('fill'): out.append('background-color:' + _rgba(style['fill']))
    if style.get('opacity') is not None: out.append('opacity:' + _n(style['opacity']))
    if style.get('cornerRadii'):
        radii = style['cornerRadii']
        out.append('border-radius:' + ' '.join(_px(radii[side]) for side in ('top', 'right', 'bottom', 'left')))
    if style.get('gradients'): out.append('background-image:' + gradient_css(style['gradients']))
    out += stroke_css(style.get('strokes') or [])
    if style.get('shadows'): out.append('box-shadow:' + _shadows_css(style['shadows']))
    if style.get('blur') is not None: out.append('filter:blur(' + _px(style['blur']) + ')')
    if style.get('backdropBlur') is not None: out.append('backdrop-filter:blur(' + _px(style['backdropBlur']) + ')')
    placement = style.get('mediaPlacement')
    fit, position = style.get('mediaFit'), style.get('mediaPosition')
    if placement == 'content':
        if fit in ('contain', 'cover', 'stretch', 'original'):
            out.append('object-fit:' + {'stretch': 'fill', 'original': 'none'}.get(fit, fit))
        if position: out.append('object-position:%s%% %s%%' % (_n(position['x'] * 100), _n(position['y'] * 100)))
    elif placement in ('background', 'surface'):
        out.append('background-repeat:no-repeat')
        if fit in ('contain', 'cover', 'stretch', 'original'):
            out.append('background-size:' + {'stretch': '100% 100%', 'original': 'auto'}.get(fit, fit))
        if position: out.append('background-position:%s%% %s%%' % (_n(position['x'] * 100), _n(position['y'] * 100)))
    if style.get('family'):
        family = style['family']
        out.append('font-family:' + (family if family.lower() in GENERIC_FONTS else _css_string(family) + ',sans-serif'))
    if style.get('size') is not None: out.append('font-size:' + _px(style['size']))
    if style.get('weight') is not None: out.append('font-weight:' + _n(style['weight']))
    if style.get('lineHeight') is not None: out.append('line-height:' + _px(style['lineHeight']))
    if style.get('letterSpacing') is not None: out.append('letter-spacing:' + _px(style['letterSpacing']))
    if style.get('color'): out.append('color:' + _rgba(style['color']))
    if style.get('italic') is not None: out.append('font-style:' + ('italic' if style['italic'] else 'normal'))
    if style.get('casing'): out.append('text-transform:' + style['casing'])
    if style.get('alignment'): out.append('text-align:' + style['alignment'])
    if style.get('direction') in ('ltr', 'rtl'): out.append('direction:' + style['direction'])
    if style.get('decorations') is not None:
        out.append('text-decoration-line:' + (' '.join({'strikethrough': 'line-through'}.get(item, item)
                                                        for item in style['decorations']) or 'none'))
    if style.get('wrapping'):
        out.append('white-space:' + {'wrap': 'normal', 'noWrap': 'nowrap', 'preserve': 'pre-wrap'}[style['wrapping']])
    if style.get('maximumLines'):
        out += ['display:-webkit-box', '-webkit-box-orient:vertical', '-webkit-line-clamp:' + str(style['maximumLines']),
                'overflow:hidden']
    return out


def _frame_keys(frame):
    """Content fingerprints for cross-frame matching; frame-local node IDs are not stable identities."""
    nodes = {node['id']: node for node in frame['nodes']}
    kids = {}
    for node in frame['nodes']:
        kids.setdefault(node.get('parentId'), []).append(node['id'])
    own = {node_id: '' if (node.get('semantics') or {}).get('sensitive') else
           ''.join(run.get('text', '') for run in node.get('text', [])) for node_id, node in nodes.items()}
    subtree = {}

    def text_of(node_id, depth=0):
        if node_id not in subtree:
            parts = [own[node_id]] + ([text_of(child, depth + 1) for child in kids.get(node_id, [])] if depth < 200 else [])
            subtree[node_id] = ' '.join(part for part in parts if part)[:160]
        return subtree[node_id]

    def label(node_id):
        node = nodes[node_id]
        semantics = node.get('semantics') or {}
        return [node['kind'], semantics.get('role'), None if semantics.get('sensitive') else semantics.get('name'), own[node_id][:60]]

    def anchor(node_id):
        parent, seen = nodes[node_id].get('parentId'), set()
        while parent in nodes and parent not in seen:
            seen.add(parent)
            value = label(parent)
            if any(value[1:]): return value
            parent = nodes[parent].get('parentId')
        return None

    strong, weak = {}, {}
    for node_id in nodes:
        own_label, context, text = label(node_id), anchor(node_id), text_of(node_id)
        if any(own_label[1:]) or text or context:
            strong[node_id] = json.dumps([own_label, text, context])
        if any(own_label[1:]):
            weak[node_id] = json.dumps([own_label, context])
    return nodes, strong, weak


def match_frames(source, target):
    """source node ID -> target node ID; never by raw ID. Passes: full fingerprint, labeled node + context,
    then anonymous nodes only when the parent is matched, kind and x/y/width agree and the candidate is unique."""
    a_nodes, a_strong, a_weak = _frame_keys(source)
    b_nodes, b_strong, b_weak = _frame_keys(target)
    pairs = {}
    for a_keys, b_keys in ((a_strong, b_strong), (a_weak, b_weak)):
        pending = {}
        used = set(pairs.values())
        for node_id in b_nodes:
            if node_id not in used and node_id in b_keys: pending.setdefault(b_keys[node_id], []).append(node_id)
        for node_id in a_nodes:
            if node_id in pairs or node_id not in a_keys: continue
            candidates = pending.get(a_keys[node_id])
            if candidates: pairs[node_id] = candidates.pop(0)

    def box(node):
        return fact((node.get('geometry') or {}).get('visualBounds'))

    def same_box(left, right):
        return left == right or (left and right and all(abs(left[key] - right[key]) <= 2 for key in ('x', 'y', 'width')))

    b_children = {}
    for node_id, node in b_nodes.items():
        b_children.setdefault(node.get('parentId'), []).append(node_id)
    for node_id, node in a_nodes.items():
        if node_id in pairs or node_id in a_weak: continue
        parent = node.get('parentId')
        if parent is not None and parent not in pairs: continue
        used = set(pairs.values())
        candidates = [other for other in b_children.get(pairs.get(parent), []) if other not in used and
                      other not in b_weak and b_nodes[other]['kind'] == node['kind'] and same_box(box(node), box(b_nodes[other]))]
        if len(candidates) == 1: pairs[node_id] = candidates[0]
    return pairs


def _top(ids, nodes):
    chosen = set(ids)
    return [node_id for node_id in ids if nodes[node_id].get('parentId') not in chosen]


def _mostly_matched(nodes, matched, root, share=0.2):
    subtree = _descendants(nodes, root)
    return sum(node_id in matched for node_id in subtree) > share * len(subtree)


def _descendants(nodes, root):
    kids = {}
    for node_id, node in nodes.items():
        kids.setdefault(node.get('parentId'), []).append(node_id)
    result, pending = [], [root]
    while pending:
        node_id = pending.pop()
        result.append(node_id)
        pending += kids.get(node_id, [])
    return result


def plan_wiring(scene, frame):
    """Map each measured `changed` transition onto the scaffold frame as show/hide/aria effects.
    Revealed content absent from the scaffold frame is injected (hidden) under its matched parent;
    hide targets may resolve into such injected content from another transition."""
    frames = {item['id']: item for item in scene['frames']}
    entry_nodes = {node['id']: node for node in frame['nodes']}
    cache = {}

    def matched(source, target):
        if source is target:
            return {node['id']: node['id'] for node in source['nodes']}
        key = (source['id'], target['id'])
        if key not in cache: cache[key] = match_frames(source, target)
        return cache[key]

    planned, unwired, injections, injected_ids = [], [], {}, {}
    for transition in scene.get('transitions', []):
        base = {'transition': transition['id'], 'nodeId': transition.get('nodeId'), 'action': transition['action']}
        if transition.get('kind') != 'measured' or transition.get('outcome') != 'changed':
            unwired.append({**base, 'reason': 'outcome ' + str(transition.get('outcome')) + ' (' + str(transition.get('kind')) + ')'}); continue
        if transition['action'] not in EVENTS:
            unwired.append({**base, 'reason': 'action has no DOM event mapping'}); continue
        before, after = frames.get(transition['fromFrameId']), frames.get(transition.get('toFrameId'))
        if before is None or after is None:
            unwired.append({**base, 'reason': 'destination frame not exported'}); continue
        to_entry = matched(before, frame)
        trigger = to_entry.get(transition.get('nodeId'))
        if trigger is None:
            unwired.append({**base, 'reason': 'trigger not matched in scaffold frame'}); continue
        b_nodes = {node['id']: node for node in before['nodes']}
        a_nodes = {node['id']: node for node in after['nodes']}
        to_after, after_entry = matched(before, after), matched(after, frame)
        from_after = {value: key for key, value in to_after.items()}
        b_vis = {key: visibility(node, b_nodes) for key, node in b_nodes.items()}
        a_vis = {key: visibility(node, a_nodes) for key, node in a_nodes.items()}
        shown = [key for key in a_nodes if a_vis[key] is not False and (key not in from_after or b_vis[from_after[key]] is False)]
        hidden = [key for key in b_nodes if b_vis[key] is not False and (key not in to_after or a_vis[to_after[key]] is False)]
        shown = [key for key in shown if key in after_entry or not _mostly_matched(a_nodes, after_entry, key)]
        hidden = [key for key in hidden if key in to_after or not _mostly_matched(b_nodes, to_after, key)]
        show, missing = [], 0
        for key in _top(shown, a_nodes):
            if key in after_entry:
                show.append(after_entry[key]); continue
            parent = a_nodes[key].get('parentId')
            while parent and parent not in after_entry: parent = a_nodes[parent].get('parentId')
            if parent is None:
                missing += 1; continue
            siblings = [item for item in a_nodes if a_nodes[item].get('parentId') == a_nodes[key].get('parentId')]
            following = next((after_entry[item] for item in siblings[siblings.index(key) + 1:] if item in after_entry), None)
            injections.setdefault((after['id'], key), (after_entry[parent], following))
            injected_ids.setdefault(after['id'], set()).update(_descendants(a_nodes, key))
            show.append(after['id'] + ':' + key)
        aria = []
        for key, target in to_after.items():
            if key not in to_entry: continue
            old, new = b_nodes[key].get('semantics') or {}, a_nodes[target].get('semantics') or {}
            for field, attribute in (('selected', 'aria-selected'), ('expanded', 'aria-expanded'), ('checked', 'aria-checked')):
                if new.get(field) is not None and new.get(field) != old.get(field):
                    aria.append([to_entry[key], attribute, str(new[field]).lower()])
        planned.append((transition, trigger, before, b_nodes, _top(hidden, b_nodes), show, aria, missing))
    wired = []
    for transition, trigger, before, b_nodes, hidden, show, aria, missing in planned:
        to_entry, hide = matched(before, frame), []
        for key in hidden:
            if key in to_entry:
                hide.append(to_entry[key]); continue
            for other_id, ids in injected_ids.items():
                target = matched(before, frames[other_id]).get(key)
                if target in ids:
                    hide.append(other_id + ':' + target); break
        hide = [item for item in hide if item not in show]
        if not (show or hide or aria):
            unwired.append({'transition': transition['id'], 'nodeId': transition.get('nodeId'), 'action': transition['action'],
                            'reason': 'no visible effect matched in scaffold frame'}); continue
        role = normalized_role(entry_nodes[trigger].get('semantics'))
        if transition['action'] == 'hover': mode = 'hover'
        elif role == 'tab': mode = 'select'
        elif show: mode = 'toggle'
        elif hide: mode = 'dismiss'
        else: mode = 'apply'
        wired.append({'transition': transition['id'], 'trigger': trigger, 'event': EVENTS[transition['action']], 'mode': mode,
                      'show': show, 'hide': hide, 'aria': aria, 'injected': sum(':' in item for item in show),
                      'unmatchedTargets': missing})
    return wired, unwired, injections



WIRING_JS = r'''
const byId = (id) => document.querySelector('[data-node-id="' + CSS.escape(id) + '"]');
const initial = new Map();
const remember = (element) => { if (element && !initial.has(element)) initial.set(element, element.hidden); };
const active = new Set();
function setState(wiring, on) {
  wiring.show.forEach((id) => { const e = byId(id); remember(e); if (e) e.hidden = on ? false : initial.get(e); });
  wiring.hide.forEach((id) => { const e = byId(id); remember(e); if (e) e.hidden = on ? true : initial.get(e); });
  wiring.aria.forEach(([id, attribute, value]) => {
    const e = byId(id);
    if (!e) return;
    if (!e.hasAttribute("data-initial-" + attribute)) e.setAttribute("data-initial-" + attribute, e.getAttribute(attribute) ?? "");
    const original = e.getAttribute("data-initial-" + attribute);
    if (on) e.setAttribute(attribute, value); else if (original) e.setAttribute(attribute, original); else e.removeAttribute(attribute);
  });
  if (on) active.add(wiring); else active.delete(wiring);
}
WIRING.forEach((wiring) => {
  const trigger = byId(wiring.trigger);
  if (!trigger) return;
  if (wiring.mode === "hover") {
    const targets = () => [trigger, ...wiring.show.map(byId)].filter(Boolean);
    trigger.addEventListener("mouseenter", () => setState(wiring, true));
    trigger.addEventListener("focusin", () => setState(wiring, true));
    const leave = (event) => { if (!targets().some((e) => e.contains(event.relatedTarget))) setState(wiring, false); };
    targets().forEach((e) => e.addEventListener("mouseleave", leave));
    return;
  }
  trigger.addEventListener(wiring.event, (event) => {
    if (trigger.tagName === "A") event.preventDefault();
    if (wiring.mode === "toggle") { setState(wiring, !active.has(wiring)); return; }
    if (wiring.mode === "select") {
      WIRING.filter((other) => other !== wiring && other.mode === "select" && active.has(other)).forEach((other) => setState(other, false));
    }
    setState(wiring, true);
  });
});
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  [...active].filter((w) => w.mode === "toggle" || w.mode === "hover").forEach((w) => setState(w, false));
});
document.addEventListener("click", (event) => {
  [...active].filter((w) => w.mode === "toggle").forEach((w) => {
    const inside = [w.trigger, ...w.show].map(byId).some((e) => e && e.contains(event.target));
    if (!inside) setState(w, false);
  });
});
document.querySelectorAll("[data-unverified]").forEach((element) => {
  element.addEventListener("click", (event) => { if (element.tagName === "A") event.preventDefault(); });
});
'''


DIALOG_STATE = re.compile(r'consent|cookie|dialog|modal|banner|popup|overlay', re.I)


def choose_frame(document, frame_id=None):
    """Explicit --frame wins. Otherwise prefer the observed default-state frame (same profile) over an entry
    frame that is a consent/dialog pre-dismiss state or shows a visible dialog/modal."""
    scene, entry, nodes = select(document, frame_id)
    if frame_id is not None:
        return entry['id'], {'selected': entry['id'], 'entry': scene.get('entryFrameId'), 'reason': 'explicit --frame'}
    dialogs = [node_id for node_id, node in nodes.items() if visibility(node, nodes) is not False and
               (normalized_role(node.get('semantics')) == 'dialog' or (node.get('semantics') or {}).get('modal'))]
    defaults = [frame for frame in scene['frames'] if frame.get('stateId') == 'default' and frame['id'] != entry['id']
                and frame.get('profileId') == entry.get('profileId')]
    state = entry.get('stateId') or ''
    if defaults and (DIALOG_STATE.search(state) or dialogs):
        why = 'entry state %r is a pre-dismiss consent/dialog state' % state if DIALOG_STATE.search(state) else \
            'entry frame shows dialog %s' % ', '.join(dialogs[:2])
        return defaults[0]['id'], {'selected': defaults[0]['id'], 'entry': entry['id'],
                                   'reason': why + '; using the observed default-state frame (override with --frame)'}
    return entry['id'], {'selected': entry['id'], 'entry': entry['id'], 'reason': 'entry frame'}


def scaffold(document, output, frame_id=None, base_url=None):
    frame_id, frame_choice = choose_frame(document, frame_id)
    scene, frame, raw = select(document, frame_id)
    raw = dict(raw)
    table = ({}, {})
    spec = implementation_spec(document, frame['id'], redact=True, table=table)
    nodes = {node['id']: node for node in spec['nodes']}
    frame_node_ids = set(nodes)
    styles = table[0]
    children = {node_id: [] for node_id in nodes}
    roots = []
    for node in spec['nodes']:
        (children[node['parentId']] if node.get('parentId') else roots).append(node['id'])
    wired, unwired, injections = plan_wiring(scene, frame)
    injected_roots, other_specs = set(), {}
    for (other_id, root_id), (parent, following) in injections.items():
        if other_id not in other_specs:
            other = implementation_spec(document, other_id, redact=True, table=table)
            other_kids = {}
            for item in other['nodes']:
                other_kids.setdefault(item.get('parentId'), []).append(item['id'])
            other_specs[other_id] = ({item['id']: item for item in other['nodes']}, select(document, other_id)[2], other_kids)
        other_nodes, other_raw, other_kids = other_specs[other_id]
        pending = [(root_id, parent)]
        first = True
        while pending:
            node_id, parent_id = pending.pop(0)
            prefixed = other_id + ':' + node_id
            if prefixed in nodes: continue
            nodes[prefixed] = {**other_nodes[node_id], 'id': prefixed, 'parentId': parent_id}
            raw[prefixed] = other_raw[node_id]
            children[prefixed] = []
            siblings = children[parent_id]
            if first and following in siblings and nodes[following].get('parentId') == parent_id:
                siblings.insert(siblings.index(following), prefixed)
            else:
                siblings.append(prefixed)
            first = False
            pending += [(child, prefixed) for child in other_kids.get(node_id, [])]
        injected_roots.add(other_id + ':' + root_id)
    transition_actions = {transition['id']: transition['action'] for transition in scene.get('transitions', [])}
    wired_actions = {}
    for item in wired:
        wired_actions.setdefault(item['trigger'], {}).setdefault(transition_actions[item['transition']], []).append(item['transition'])
    output.mkdir(parents=True, exist_ok=True)
    copied = copy_assets(document, output / 'assets', base_url)
    asset_url = {key: 'assets/' + Path(value['file']).name for key, value in copied.items() if value.get('available')}
    resources = {resource['id']: resource for resource in scene.get('resources', [])}
    profile = next((item for item in scene['profiles'] if item.get('id') == frame.get('profileId')), {})
    extent = frame.get('extent') or {}

    def layout(node_id):
        return styles.get(nodes[node_id].get('layoutStyle'), {}) if node_id else {}

    def bounds(node_id):
        if node_id is None:
            return {'x': extent.get('x', 0), 'y': extent.get('y', 0)}
        return (nodes[node_id].get('geometry') or {}).get('visualBounds')

    def arrangement(node_id):
        if node_id is None:
            return 'single' if len(roots) == 1 else None
        return layout(node_id).get('arrangement')

    signatures = {}

    def signature(node_id):
        if node_id not in signatures:
            node = nodes[node_id]
            signatures[node_id] = json.dumps([node['kind'], normalized_role(node.get('semantics')), node.get('layoutStyle'),
                                              node.get('paintStyle'), bool(node.get('mediaResourceId')),
                                              [run.get('typographyStyle') for run in node['text']],
                                              [signature(child) for child in children[node_id]]])
        return signatures[node_id]

    def subtree_size(node_id):
        return 1 + sum(subtree_size(child) for child in children[node_id])

    components, component_of = [], {}
    for parent in [None] + list(nodes):
        groups = {}
        for child in (roots if parent is None else children[parent]):
            groups.setdefault(signature(child), []).append(child)
        for members in groups.values():
            if len(members) >= 2 and subtree_size(members[0]) >= 2:
                identifier = 'c' + str(len(components))
                components.append({'id': identifier, 'parentId': parent, 'count': len(members),
                                   'nodesPerInstance': subtree_size(members[0]), 'instances': members[:8]})
                for member in members: component_of[member] = identifier

    report = {'nodesEmitted': 0, 'nodesSkipped': [], 'redactedSensitive': [], 'hiddenNodes': [], 'unknownVisibility': 0,
              'absoluteFallbackParents': [], 'scrollContainers': [], 'scrollSizedFromBounds': [], 'mediaSizedFromBounds': [],
              'widthFilledFromBounds': [], 'textWidthFromBounds': [], 'minHeightFromBounds': [], 'spacingFromBounds': [], 'backgroundLayersInferred': [],
              'stickyWithoutAnchors': [], 'paintOrderUnknownOverlaps': [],
              'paintOrderUnknownOverlapCount': 0,
              'missingMedia': [], 'missingFonts': [], 'unresolvedMasks': [], 'actionsWithoutObservedOutcome': [],
              'inlineRunsOrderInferred': [], 'statesSkipped': [], 'breakpointRules': 0}
    node_rules, html_lines = [], []
    used_styles = set()

    def area(left, right):
        if not left or not right: return 0
        width = min(left['x'] + left['width'], right['x'] + right['width']) - max(left['x'], right['x'])
        height = min(left['y'] + left['height'], right['y'] + right['height']) - max(left['y'], right['y'])
        return width * height if width > 0 and height > 0 else 0

    overlaps = []
    for parent in [None] + [node_id for node_id in nodes if node_id in frame_node_ids]:
        siblings = [child for child in (roots if parent is None else children[parent]) if child in frame_node_ids]
        if arrangement(parent) not in (None, 'unknown') or len(siblings) < 2: continue
        for index, first in enumerate(siblings):
            for second in siblings[index + 1:]:
                if any((nodes[item].get('geometry') or {}).get('paintOrder') is not None for item in (first, second)): continue
                if area(bounds(first), bounds(second)) > 1 and nodes[first].get('effectiveVisible') is not False \
                        and nodes[second].get('effectiveVisible') is not False:
                    overlaps.append([first, second])
    report['paintOrderUnknownOverlaps'] = overlaps[:20]
    report['paintOrderUnknownOverlapCount'] = len(overlaps)
    dialogs = [node_id for node_id in frame_node_ids if nodes[node_id].get('effectiveVisible') is not False and
               (normalized_role(nodes[node_id].get('semantics')) == 'dialog' or (nodes[node_id].get('semantics') or {}).get('modal'))]
    default_frames = [item['id'] for item in scene['frames'] if item.get('stateId') == 'default' and item['id'] != frame['id']]

    spacing_cache = {}

    def spacing(node_id):
        """Fallback for start-aligned rows/columns without a gap fact: reproduce measured spacing between
        consecutive flow children (margins are not part of the Scene contract)."""
        if node_id in spacing_cache: return spacing_cache[node_id]
        result, style = {}, layout(node_id)
        axis = {'row': ('x', 'width', 'columnGap', 'margin-left'), 'column': ('y', 'height', 'rowGap', 'margin-top')}.get(style.get('arrangement'))
        if axis and not style.get('reverse') and not style.get('wrap') and style.get(axis[2]) is None and \
                style.get('mainAlignment') in (None, 'start', 'unknown', 'stretch', 'baseline'):
            start, size, _, margin = axis
            items = [child for child in children[node_id] if child in frame_node_ids and bounds(child) and
                     nodes[child].get('effectiveVisible') is not False and layout(child).get('placement') not in ('overlay', 'anchored', 'sticky')]
            spaces = [bounds(item)[start] - (bounds(previous)[start] + bounds(previous)[size]) for previous, item in zip(items, items[1:])]
            if spaces and min(spaces) >= -0.5 and max(spaces) > 0.5:
                if max(spaces) - min(spaces) <= 0.5:
                    result['gap'] = round(sum(spaces) / len(spaces), 2)
                else:
                    result['margins'] = {item: margin + ':' + _px(round(space, 2)) for item, space in zip(items[1:], spaces) if space > 0.5}
                report['spacingFromBounds'].append(node_id)
        spacing_cache[node_id] = result
        return result

    background_cache = {}

    def background_layer(node_id):
        """Overlay without paint order covering >=90% of its parent, with media and no text/controls below it."""
        if node_id not in background_cache:
            node, parent = nodes[node_id], nodes[node_id].get('parentId')
            result = False
            if parent and node_id in frame_node_ids and (node.get('geometry') or {}).get('paintOrder') is None and \
                    layout(node_id).get('placement') in ('overlay', 'anchored') and \
                    sum(1 for child in children[parent] if child in frame_node_ids) > 1:
                own, outer = bounds(node_id), bounds(parent)
                if own and outer and outer.get('width') and outer.get('height'):
                    width = min(own['x'] + own['width'], outer['x'] + outer['width']) - max(own['x'], outer['x'])
                    height = min(own['y'] + own['height'], outer['y'] + outer['height']) - max(own['y'], outer['y'])
                    covers = width > 0 and height > 0 and width * height >= 0.9 * outer['width'] * outer['height']
                    pending, media, content = [node_id], False, False
                    while pending and covers and not content:
                        current = nodes[pending.pop()]
                        media |= bool(current.get('mediaResourceId')) or current['kind'] == 'media'
                        content |= bool(current['text']) or bool(current.get('actions')) or current['kind'] == 'control'
                        pending += children[current['id']]
                    result = covers and media and not content
            background_cache[node_id] = result
        return background_cache[node_id]

    inline_cache = {}

    def inline_child(node_id):
        """Text or link nested in a node that has its own text: an inline run of that paragraph, not a positioned block."""
        if node_id not in inline_cache:
            node, parent = nodes[node_id], nodes[node_id].get('parentId')
            result = bool(parent) and bool(nodes[parent]['text']) and not node.get('mediaResourceId')
            pending, text = [node_id], False
            while pending and result:
                current = nodes[pending.pop()]
                result = current['kind'] in ('text', 'container', 'control') and not current.get('mediaResourceId') and                     normalized_role(current.get('semantics') or {}) in (None, 'link', 'text', 'generic', 'emphasis', 'strong')
                text |= bool(current['text'])
                pending += children[current['id']]
            inline_cache[node_id] = result and text
        return inline_cache[node_id]

    def leads_runs(node_id):
        """Inline child measured on its parent's first line goes before the parent's own text; others follow it."""
        own, outer = bounds(node_id), bounds(nodes[node_id]['parentId'])
        return bool(own and outer) and own['y'] - outer['y'] < max(own['height'] / 2, 1)

    def node_css(node_id):
        if inline_child(node_id):
            return ['display:inline']
        node, style, parent = nodes[node_id], layout(node_id), nodes[node_id].get('parentId')
        out, box, origin = [], bounds(node_id), bounds(parent)
        parent_mode = arrangement(parent)
        placement = style.get('placement')
        absolute = placement in ('overlay', 'anchored') or parent_mode in (None, 'unknown', 'free')
        if absolute and parent_mode in (None, 'unknown') and placement not in ('overlay', 'anchored'):
            fallback = parent if parent else '#scene-root'
            if fallback not in report['absoluteFallbackParents']: report['absoluteFallbackParents'].append(fallback)
        anchors = style.get('anchors') or {}
        if absolute:
            if placement not in ('overlay', 'anchored'): out.append('position:absolute')
            if any(anchors.get(side) is not None for side in ('top', 'right', 'bottom', 'left')):
                out += [side + ':' + _px(anchors[side]) for side in ('top', 'right', 'bottom', 'left') if anchors.get(side) is not None]
            elif box and origin is not None:
                out += ['left:' + _px(box['x'] - origin['x']), 'top:' + _px(box['y'] - origin['y'])]
        paint_order = (node.get('geometry') or {}).get('paintOrder')
        if paint_order is not None and (absolute or parent_mode in ('row', 'column', 'grid', 'stack')):
            out.append('z-index:' + str(int(paint_order)))
        elif background_layer(node_id):
            out.append('z-index:-1')
            report['backgroundLayersInferred'].append(node_id)
        elif absolute and parent_mode in (None, 'unknown'):
            if placement in ('overlay', 'anchored', 'sticky'): out.append('z-index:1')
        if children[node_id] and (arrangement(node_id) in (None, 'unknown', 'free') or any(
                background_layer(child) for child in children[node_id])):
            out.append('isolation:isolate')
        if placement == 'sticky':
            if anchors.get('top') is not None: out.append('top:' + _px(anchors['top']))
            elif anchors.get('bottom') is not None: out.append('bottom:' + _px(anchors['bottom']))
            else: report['stickyWithoutAnchors'].append(node_id)
        sized = {axis: (style.get(axis) or {}).get('policy') == 'fixed' and (style.get(axis) or {}).get('value') is not None
                 for axis in ('horizontal', 'vertical')}
        child_absolute = arrangement(node_id) in (None, 'unknown', 'free') and children[node_id] or any(
            layout(child).get('placement') in ('overlay', 'anchored') for child in children[node_id])
        if child_absolute and not absolute and placement != 'sticky': out.append('position:relative')
        if box and (absolute or (child_absolute and arrangement(node_id) in (None, 'unknown', 'free'))):
            if not sized['horizontal']: out.append('width:' + _px(box['width']))
            if not sized['vertical']: out.append('height:' + _px(box['height']))
            sized = {'horizontal': True, 'vertical': True}
        for axis in ('horizontal', 'vertical'):
            if (style.get(axis) or {}).get('policy') != 'fill': continue
            main = {'row': 'horizontal', 'column': 'vertical'}.get(parent_mode)
            if main == axis: out += ['flex:1 1 0', 'min-' + ('width' if axis == 'horizontal' else 'height') + ':0']
            elif main: out.append('align-self:stretch')
            else: out.append(('width' if axis == 'horizontal' else 'height') + ':100%')
        parent_box = bounds(parent) if parent else None
        flow = not absolute and placement not in ('sticky',) and box
        if flow and parent_box and 'width' in parent_box and not sized['horizontal'] and \
                (style.get('horizontal') or {}).get('policy') != 'fill':
            padding = layout(parent).get('padding') or {}
            content = parent_box['width'] - padding.get('left', 0) - padding.get('right', 0)
            visible_siblings = [child for child in children[parent] if child in frame_node_ids and
                                nodes[child].get('effectiveVisible') is not False and
                                layout(child).get('placement') not in ('overlay', 'anchored')]
            if abs(box['width'] - content) <= 1 and (parent_mode in ('column', 'grid', 'stack', 'single') or
                                                     (parent_mode == 'row' and len(visible_siblings) == 1)):
                out.append('width:100%')
                report['widthFilledFromBounds'].append(node_id)
            elif node['text'] and not children[node_id] and box['width'] < content - 1 and \
                    parent_mode in ('column', 'grid', 'stack', 'single'):
                out.append('max-width:' + _px(round(box['width'] + 1, 2)))
                report['textWidthFromBounds'].append(node_id)
        if flow and children[node_id] and not sized['vertical'] and style.get('clip') != 'scroll' and node_id in frame_node_ids:
            vertical = style.get('vertical') or {}
            height = min(box['height'], vertical['maximum']) if vertical.get('maximum') is not None else box['height']
            if height > (vertical.get('minimum') or 0) + 0.5:
                out.append('min-height:' + _px(height))
                report['minHeightFromBounds'].append(node_id)
        gap, margins = spacing(node_id), spacing(parent).get('margins', {}) if parent else {}
        if gap.get('gap') is not None:
            out.append(('column-gap:' if style.get('arrangement') == 'row' else 'row-gap:') + _px(gap['gap']))
        if node_id in margins:
            out.append(margins[node_id])
        media = node.get('mediaResourceId')
        constrained = any(sized[axis] or (style.get(axis) or {}).get('policy') == 'fill' for axis in sized)
        if media and not children[node_id] and box and not constrained:
            out += ['width:' + _px(box['width']), 'height:' + _px(box['height'])]
            report['mediaSizedFromBounds'].append(node_id)
        if style.get('clip') == 'scroll':
            report['scrollContainers'].append(node_id)
            axis = 'horizontal' if style.get('arrangement') == 'row' else 'vertical'
            if not sized[axis] and box:
                out.append(('max-width:' if axis == 'horizontal' else 'max-height:') + _px(box['width' if axis == 'horizontal' else 'height']))
                report['scrollSizedFromBounds'].append(node_id)
        paint = styles.get(node.get('paintStyle'), {})
        if media and media in asset_url and paint.get('mediaPlacement') in ('background', 'surface'):
            out.append('background-image:url(' + _css_string(asset_url[media]) + ')')
        return out

    def emit(node_id, depth):
        node, raw_node = nodes[node_id], raw[node_id]
        semantics = node.get('semantics') or {}
        role = normalized_role(semantics)
        paint = styles.get(node.get('paintStyle'), {})
        media = node.get('mediaResourceId')
        kids = children[node_id]
        content_media = media and paint.get('mediaPlacement', 'content') == 'content'
        media_kind = (resources.get(media) or {}).get('kind')
        tag = ROLE_TAGS.get(role) or ('p' if node['kind'] == 'text' else 'div')
        if content_media and role in (None, 'img', 'image'):
            tag = 'video' if media_kind == 'video' else 'img'
        if tag == 'li' and normalized_role((nodes.get(node.get('parentId')) or {}).get('semantics')) != 'list': tag = 'div'
        if inline_child(node_id) and tag not in ('a', 'button'): tag = 'span'
        if tag in VOID_TAGS and kids: tag = 'div'
        if tag == 'input' and role == 'textbox' and node['text'] and semantics.get('editable') is False: tag = 'div'
        classes = [node[key] for key in ('layoutStyle', 'paintStyle') if node.get(key) and style_css(styles[node[key]])]
        if tag == 'input':
            classes += [run['typographyStyle'] for run in node['text'][:1] if run.get('typographyStyle')]
        used_styles.update(classes)
        attrs = [('data-node-id', node_id)]
        if classes: attrs.append(('class', ' '.join(classes)))
        if layout(node_id).get('arrangement') in ('stack', 'free'): attrs.append(('data-arrangement', layout(node_id)['arrangement']))
        if role and IMPLICIT_ROLES.get(tag) != role and not (tag == 'input' and role in ('textbox', 'checkbox')):
            attrs.append(('role', role))
        if role == 'heading' and tag != 'h2': attrs.append(('aria-level', '2'))
        if semantics.get('name') and tag != 'img' and semantics['name'] != ''.join(run['text'] for run in node['text']):
            attrs.append(('aria-label', semantics['name']))
        for key, aria in (('selected', 'aria-selected'), ('expanded', 'aria-expanded'), ('modal', 'aria-modal'), ('checked', 'aria-checked')):
            if semantics.get(key) is not None and not (key == 'checked' and tag == 'input'): attrs.append((aria, str(semantics[key]).lower()))
        if semantics.get('enabled') is False: attrs.append(('disabled' if tag in ('button', 'input') else 'aria-disabled', '' if tag in ('button', 'input') else 'true'))
        if semantics.get('sensitive'):
            attrs.append(('data-sensitive', 'redacted'))
            report['redactedSensitive'].append(node_id)
        if tag == 'button': attrs.append(('type', 'button'))
        if tag == 'a': attrs.append(('href', '#'))
        if node.get('actions') and tag in ('div', 'p', 'section'): attrs.append(('tabindex', '0'))
        if tag == 'input':
            attrs.append(('type', 'checkbox' if role == 'checkbox' else 'text'))
            if role == 'checkbox' and semantics.get('checked'): attrs.append(('checked', ''))
            text = ''.join(run['text'] for run in node['text'])
            if text: attrs.append(('value', text))
            elif role in ('textbox', 'searchbox', 'combobox') and semantics.get('name'):
                attrs.append(('placeholder', semantics['name']))
        if media:
            if media in asset_url:
                if tag in ('img', 'video'):
                    attrs.append(('src', asset_url[media]))
                    if tag == 'img': attrs.append(('alt', semantics.get('name') or ''))
                    else: attrs += [('muted', ''), ('playsinline', ''), ('autoplay', ''), ('loop', '')]
            else:
                attrs.append(('data-missing-media', media))
                report['missingMedia'].append({'nodeId': node_id, 'resourceId': media})
        elif node['kind'] == 'media' and node_id in frame_node_ids:
            attrs.append(('data-missing-media', 'no-source'))
            report['missingMedia'].append({'nodeId': node_id, 'resourceId': None, 'reason': next(
                (issue['code'] for issue in scene.get('issues', []) if issue.get('nodeId') == node_id and
                 issue.get('frameId') in (None, frame['id']) and 'media' in issue['code'].lower()), 'no media resource')})
        if component_of.get(node_id): attrs.append(('data-component', component_of[node_id]))
        actions = [action['kind'] for action in node.get('actions') or []]
        if actions:
            attrs.append(('data-actions', ' '.join(actions)))
            seen = sorted({tid for ids in wired_actions.get(node_id, {}).values() for tid in ids})
            if seen: attrs.append(('data-transitions', ' '.join(seen)))
            inert = [action for action in actions if action not in wired_actions.get(node_id, {})]
            if inert:
                attrs.append(('data-unverified', ' '.join(inert)))
                if node_id in frame_node_ids:
                    report['actionsWithoutObservedOutcome'] += [{'nodeId': node_id, 'action': action} for action in inert]
        if node_id in injected_roots: attrs.append(('data-injected-from', node_id.split(':')[0]))
        visible = fact(raw_node.get('visible'))
        if visible is False or node_id in injected_roots:
            attrs.append(('hidden', ''))
            report['hiddenNodes'].append(node_id)
        elif node.get('effectiveVisible') is None:
            report['unknownVisibility'] += 1
        css = node_css(node_id)
        if css: node_rules.append(_rule('[data-node-id=' + _css_string(node_id) + ']', css))
        emitted.add(node_id)
        rendered = ''.join(' ' + key + ('' if value == '' and key in ('hidden', 'checked', 'disabled', 'muted', 'playsinline', 'autoplay', 'loop')
                                        else '="' + html.escape(str(value), quote=True) + '"') for key, value in attrs)
        pad = '  ' * depth
        if tag in VOID_TAGS:
            html_lines.append(pad + '<' + tag + rendered + ('></video>' if tag == 'video' else '>'))
            return
        html_lines.append(pad + '<' + tag + rendered + '>')
        if content_media and tag == 'div' and media in asset_url:
            media_class = node.get('paintStyle') or ''
            html_lines.append(pad + '  <img data-media-of="%s" src="%s" alt=""%s>' % (
                html.escape(node_id), asset_url[media], ' class="' + media_class + '"' if media_class in used_styles else ''))
        inline = [child for child in kids if inline_child(child)]
        leading = [child for child in inline if leads_runs(child)]
        if inline and node['text']:
            report['inlineRunsOrderInferred'].append({'nodeId': node_id, 'before': leading,
                                                      'after': [child for child in inline if child not in leading]})
        for child in leading:
            emit(child, depth + 1)
        if tag != 'input':
            for index, run in enumerate(node['text']):
                run_class = run.get('typographyStyle')
                if run_class and style_css(styles[run_class]): used_styles.add(run_class)
                else: run_class = None
                html_lines.append(pad + '  <span data-run="%d"%s>%s</span>' % (
                    index, ' class="' + run_class + '"' if run_class else '', html.escape(run['text'])))
        for child in kids:
            if child not in leading:
                emit(child, depth + 1)
        html_lines.append(pad + '</' + tag + '>')

    emitted = set()
    for root in roots:
        emit(root, 2)
    report['nodesEmitted'] = len(emitted)
    report['nodesSkipped'] = [node_id for node_id in nodes if node_id not in emitted]

    font_css, provided = [], set()
    for resource in scene.get('resources', []):
        if resource.get('kind') != 'font' or resource['id'] not in asset_url:
            continue
        faces = resource.get('fontFaces') or [{'family': style['family']} for style in styles.values()
                                               if style.get('fontResourceId') == resource['id'] and style.get('family')]
        for face in {json.dumps(face, sort_keys=True): face for face in faces}.values():
            declarations = ['font-family:' + _css_string(face['family']), 'src:url(' + _css_string(asset_url[resource['id']]) + ')',
                            'font-display:block']
            if face.get('weightMinimum') is not None:
                declarations.append('font-weight:' + _n(face['weightMinimum']) + (
                    ' ' + _n(face['weightMaximum']) if face.get('weightMaximum') not in (None, face['weightMinimum']) else ''))
            if face.get('italic') is not None: declarations.append('font-style:' + ('italic' if face['italic'] else 'normal'))
            if face.get('coverage'):
                declarations.append('unicode-range:' + ','.join('U+%X-%X' % (item['start'], item['end']) for item in face['coverage']))
            font_css.append(_rule('@font-face', declarations))
            provided.add(face['family'].lower())
    report['missingFonts'] = sorted({style['family'] for key, style in styles.items() if key in used_styles and style.get('family')
                                     and style['family'].lower() not in provided and style['family'].lower() not in GENERIC_FONTS})
    report['unresolvedMasks'] = [{'nodeId': issue.get('nodeId'), 'code': issue['code']} for issue in scene.get('issues', [])
                                 if 'mask' in issue.get('code', '').lower() and issue.get('frameId') in (None, frame['id'])]

    interaction_css, report['statesSkipped'] = state_rules(raw, emitted)
    report['statesEmitted'] = len(interaction_css)
    theme_css, report['themes'] = theme_rules(scene)
    samples_table, report['layoutSamples'] = layout_sample_table(scene, frame['id'], emitted)
    breakpoints, report['breakpointRules'] = breakpoint_css(samples_table['nodes'], report['layoutSamples'], profile.get('width'))
    if report['layoutSamples']:
        (output / 'layout-samples.json').write_text(json.dumps(samples_table, ensure_ascii=False, separators=(',', ':')),
                                                    encoding='utf-8', newline='\n')
    root_css = ['position:relative', 'margin:0 auto']
    if profile.get('width'): root_css.append('width:' + _px(profile['width']))
    if len(roots) != 1 and extent.get('height'): root_css.append('height:' + _px(extent['height']))
    css = (BASE_CSS + ''.join(font_css) + ''.join(theme_css) + _rule('#scene-root', root_css) +
           ''.join(_rule('.' + key, style_css(styles[key])) for key in styles if key in used_styles) + ''.join(node_rules) +
           ('/* Measured interaction states (scene nodes[].states). */\n' + ''.join(interaction_css) if interaction_css else ''))
    title = html.escape(str(scene['id']))
    page_html = '\n'.join(['<!doctype html>', '<html lang="%s">' % html.escape(profile.get('locale') or 'en'), '<head>',
                           '<meta charset="utf-8">',
                           '<meta name="viewport" content="width=device-width, initial-scale=1">',
                           '<title>%s</title>' % title, '<link rel="stylesheet" href="styles.css">']
                          + (['<link rel="stylesheet" href="breakpoints.css">'] if breakpoints else []) + ['</head>', '<body>',
                           '  <main id="scene-root" data-scene-id="%s" data-frame-id="%s">' % (title, html.escape(frame['id']))]
                          + html_lines + ['  </main>', '  <script src="app.js"></script>', '</body>', '</html>', ''])
    wiring_data = [{key: item[key] for key in ('transition', 'trigger', 'event', 'mode', 'show', 'hide', 'aria')} for item in wired]
    script = ('// Generated by scene_reader.py scaffold. Each WIRING entry replays one measured transition by\n'
              '// showing/hiding data-node-id targets (modes: hover, toggle + Escape/outside click, select for tabs,\n'
              '// dismiss, apply). Elements with [data-unverified] have no observed outcome and stay inert.\n'
              '"use strict";\n'
              'const WIRING = ' + json.dumps(wiring_data, ensure_ascii=False) + ';' + WIRING_JS)
    (output / 'index.html').write_text(page_html, encoding='utf-8', newline='\n')
    (output / 'styles.css').write_text(css, encoding='utf-8', newline='\n')
    (output / 'app.js').write_text(script, encoding='utf-8', newline='\n')
    if breakpoints:
        (output / 'breakpoints.css').write_text(breakpoints, encoding='utf-8', newline='\n')
    pristine = output / '.scaffold'
    pristine.mkdir(exist_ok=True)
    for name, text in (('index.html', page_html), ('styles.css', css), ('app.js', script)):
        (pristine / name).write_text(text, encoding='utf-8', newline='\n')

    def label(node_id):
        node = nodes.get(node_id) or {}
        semantics = node.get('semantics') or {}
        text = ''.join(run['text'] for run in node.get('text', [])).strip()
        return (semantics.get('name') or text or normalized_role(semantics) or node.get('kind') or '')[:48]

    visible_ids = [node_id for node_id in frame_node_ids if nodes[node_id].get('effectiveVisible') is not False]
    labelled = {item['trigger'] for item in wired} | {item['nodeId'] for item in unwired if item.get('nodeId') in nodes} | {
        item['nodeId'] for item in report['actionsWithoutObservedOutcome']}
    details = {'schema': 'reconstruct.scaffold-details.v1',
               'note': 'Machine input for todo/verify/report. Not meant for reading; use those commands.',
               'sceneId': scene['id'], 'frameId': frame['id'], 'frameState': frame.get('stateId'),
               'frameChoice': frame_choice,
               'profile': {key: profile.get(key) for key in ('id', 'width', 'height')},
               'frames': [{key: item.get(key) for key in ('id', 'stateId')} for item in scene['frames']],
               'nodeCount': len(frame_node_ids), 'injectedNodes': len(nodes) - len(frame_node_ids),
               'styleClasses': len(used_styles), **report, 'componentCandidates': components,
               'wired': wired, 'unwired': unwired, 'labels': {node_id: label(node_id) for node_id in sorted(labelled)},
               'visibleDialogs': sorted(dialogs), 'defaultFrames': default_frames,
               'truncatedFrame': frame.get('truncated', False),
               'assets': {'copied': len(asset_url), 'unavailable': sorted(set(copied) - set(asset_url))},
               'expected': {'visibleNodeIds': visible_ids,
                            'hiddenNodeIds': sorted(node_id for node_id in frame_node_ids if nodes[node_id].get('effectiveVisible') is False),
                            'scrollNodeIds': [node_id for node_id in report['scrollContainers']
                                              if node_id in frame_node_ids and nodes[node_id].get('effectiveVisible') is not False]}}
    (output / 'scaffold-details.json').write_text(json.dumps(details, ensure_ascii=False, separators=(',', ':')),
                                                  encoding='utf-8', newline='\n')
    counts = {key: (len(value) if isinstance(value, list) else value) for key, value in report.items()}
    summary = {'schema': 'reconstruct.scaffold-report.v2', 'sceneId': scene['id'], 'frameId': frame['id'],
               'frameState': frame.get('stateId'), 'frameChoice': frame_choice['reason'],
               'note': 'Summary only. Bulk lists live in scaffold-details.json (machine input, not meant for reading). '
                       'Interactions observed in the Scene are already wired; evidence gaps go to report.md, not to edits.',
               'next': ['todo --dir DIR  (exact fixes; prints DONE when nothing is left)', 'apply --dir DIR --item N',
                        'report --dir DIR --scene SCENE  (writes DIR/report.md; then append your notes)'],
               'nodes': len(frame_node_ids), 'injectedNodes': details['injectedNodes'], 'wired': len(wired),
               'unwired': len(unwired), 'inertActions': counts['actionsWithoutObservedOutcome'],
               'componentCandidates': len(components), 'absoluteFallbackParents': counts['absoluteFallbackParents'],
               'statesEmitted': report['statesEmitted'], 'statesSkipped': counts['statesSkipped'],
               'themes': [theme['id'] + (' (current)' if theme['current'] else '') for theme in report['themes']],
               'layoutSampleWidths': [sample['width'] for sample in report['layoutSamples']],
               'breakpointRules': report['breakpointRules'],
               'unplacedAtWidths': {_n(sample['width']): sample['unplaced'] for sample in report['layoutSamples'] if sample['unplaced']},
               'gaps': {key: counts[key] for key in ('missingFonts', 'missingMedia', 'unresolvedMasks',
                                                     'paintOrderUnknownOverlapCount', 'stickyWithoutAnchors', 'unknownVisibility')}
               | {'visibleDialogs': len(dialogs), 'truncatedFrame': frame.get('truncated', False)}}
    (output / 'scaffold-report.json').write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding='utf-8', newline='\n')
    return {'output': str(output), 'frameId': frame['id'], 'nodes': len(frame_node_ids), 'wired': len(wired),
            'unwired': len(unwired), 'injectedNodes': details['injectedNodes'], 'gaps': summary['gaps'],
            'next': 'todo --dir DIR; when it prints DONE run report --dir DIR --scene SCENE. Never read this script.'}


def _cap(items, limit=6):
    items = list(items)
    return ', '.join(map(str, items[:limit])) + (' (+%d more)' % (len(items) - limit) if len(items) > limit else '')


def _details(directory):
    path = Path(directory) / 'scaffold-details.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else None


class _Outline(HTMLParser):
    """Source offsets of every element, for exact snippet replacement."""

    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.text, self.starts = text, [0] + [match.end() for match in re.finditer('\n', text)]
        self.order, self.nodes, self.stack = [], {}, []
        self.feed(text)
        self.close()

    def _offset(self):
        line, column = self.getpos()
        return self.starts[line - 1] + column

    def handle_starttag(self, tag, attrs, void=False):
        start, raw = self._offset(), self.get_starttag_text()
        parent = next((item['attrs']['data-node-id'] for item in reversed(self.stack) if 'data-node-id' in item['attrs']), None)
        entry = {'tag': tag, 'attrs': {key: value or '' for key, value in attrs}, 'start': start,
                 'open_end': start + len(raw), 'end': start + len(raw), 'parent': parent}
        self.order.append(entry)
        if 'data-node-id' in entry['attrs']: self.nodes.setdefault(entry['attrs']['data-node-id'], entry)
        if not void and tag not in _Page.VOID: self.stack.append(entry)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs, void=True)

    def handle_endtag(self, tag):
        close = self.text.find('>', self._offset()) + 1
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index]['tag'] == tag:
                for entry in self.stack[index:]: entry['end'] = close
                del self.stack[index:]
                break

    def outer(self, entry):
        return self.text[entry['start']:entry['end']]

    def opening(self, entry):
        return self.text[entry['start']:entry['open_end']]


def todo_items(directory, document=None, frame_id=None):
    """Exact, deterministic fixes for verify failures (file, offsets, current snippet, replacement)."""
    directory = Path(directory)
    result = verify(directory, document, frame_id)
    checks, items, manual = result['checks'], [], []
    read = lambda name: (directory / name).read_text(encoding='utf-8') if (directory / name).is_file() else ''
    pristine_dir = directory / '.scaffold'
    index_text, css_text = read('index.html'), read('styles.css')
    page = _Outline(index_text)
    pristine = _Outline((pristine_dir / 'index.html').read_text(encoding='utf-8')) if (pristine_dir / 'index.html').is_file() else None

    def item(check, file, target, start, end, replacement, why, current=None):
        text = read(file)
        items.append({'check': check, 'file': file, 'target': target, 'start': start, 'end': end,
                      'current': text[start:end] if current is None else current, 'replacement': replacement, 'why': why})

    def restore(node_id, check, why):
        """Re-insert the scaffold's subtree for node_id after its nearest present previous sibling."""
        if pristine is None or node_id not in pristine.nodes: return False
        if any(candidate['target'] == '[data-node-id="%s"]' % node_id and candidate['start'] == candidate['end'] for candidate in items):
            return True
        source = pristine.nodes[node_id]
        parent = source['parent']
        if parent is not None and parent not in page.nodes: return False
        siblings = [entry['attrs']['data-node-id'] for entry in pristine.order
                    if 'data-node-id' in entry['attrs'] and entry['parent'] == parent]
        previous = next((other for other in reversed(siblings[:siblings.index(node_id)]) if other in page.nodes), None)
        if previous is not None: offset = page.nodes[previous]['end']
        elif parent is not None: offset = page.nodes[parent]['open_end']
        else:
            main = next((entry for entry in page.order if entry['attrs'].get('id') == 'scene-root'), None)
            if main is None: return False
            offset = main['open_end']
        item(check, 'index.html', '[data-node-id="%s"]' % node_id, offset, offset, '\n' + pristine.outer(source), why,
             current='(insert %s)' % ('after [data-node-id="%s"]' % previous if previous else 'as first child of ' +
                                     ('[data-node-id="%s"]' % parent if parent else '#scene-root')))
        return True

    if checks.get('scriptSyntax', {}).get('ok') is False:
        for error in checks['scriptSyntax'].get('errors', []):
            name = error['file']
            if (pristine_dir / name).is_file():
                item('scriptSyntax', name, name, 0, len(read(name)), (pristine_dir / name).read_text(encoding='utf-8'),
                     'Syntax error (%s); restore the scaffold copy, then re-add your change.' % error['error'],
                     current='(whole file, %d chars) %s' % (len(read(name)), error['error']))
            else: manual.append('scriptSyntax %s: %s' % (name, error['error']))
    if checks.get('localRefs', {}).get('ok') is False:
        bad = set(checks['localRefs'].get('missing', []) + checks['localRefs'].get('remote', []) + checks['localRefs'].get('outsideDir', []))
        for entry in page.order:
            if not any(entry['attrs'].get(key, '').strip()[:120] in bad for key in ('src', 'href', 'poster')): continue
            node_id = entry['attrs'].get('data-node-id')
            if node_id and pristine and node_id in pristine.nodes:
                item('localRefs', 'index.html', '[data-node-id="%s"]' % node_id, entry['start'], entry['open_end'],
                     pristine.opening(pristine.nodes[node_id]), 'Unresolvable reference; restore the scaffold tag.')
            else:
                item('localRefs', 'index.html', entry['tag'], entry['start'], entry['end'], '',
                     'Unresolvable or remote reference on an element the scaffold did not emit; remove it.')
        listed = {entry for entry in bad if any(entry in candidate['current'] for candidate in items)}
        manual += ['localRefs: %s is referenced from CSS or inline style; point it at a file in assets/' % entry for entry in sorted(bad - listed)]
    if checks.get('hiddenRespected', {}).get('ok') is False:
        for selector in checks['hiddenRespected'].get('overrides', []):
            match = re.search(r'(?:^|\})\s*([^{}]*' + re.escape(selector) + r'[^{}]*)\{[^{}]*\}', css_text)
            if match:
                start = match.start(1)
                item('hiddenRespected', 'styles.css', selector, start, match.end(), '', 'Rule overrides [hidden]; delete it.')
        if '[hidden]{display:none!important}' not in css_text.replace(' ', ''):
            item('hiddenRespected', 'styles.css', '[hidden]', 0, 0, '[hidden]{display:none!important}\n',
                 'Hidden scene nodes must stay hidden.', current='(insert at top)')
    if checks.get('sceneHiddenRespected', {}).get('ok') is False:
        shown = set(checks['sceneHiddenRespected'].get('renderedVisible', []))
        for node_id in checks['sceneHiddenRespected'].get('renderedVisible', []):
            entry, parent = page.nodes.get(node_id), page.nodes.get(node_id, {}).get('parent')
            while parent and parent not in shown: parent = page.nodes.get(parent, {}).get('parent')
            if entry is None or parent: continue
            opening = page.opening(entry)
            replacement = re.sub(r'\s*(/?)>$', r' hidden\1>', opening, count=1)
            item('sceneHiddenRespected', 'index.html', '[data-node-id="%s"]' % node_id, entry['start'], entry['open_end'],
                 replacement, 'The Scene marks this node not visible in the selected frame.')
    if checks.get('noFakeScrollbars', {}).get('ok') is False:
        for entry in page.order:
            attrs = entry['attrs']
            if attrs.get('role') == 'scrollbar' or re.search(r'scroll[-_]?(bar|thumb|track)', attrs.get('class', '') + ' ' + attrs.get('id', ''), re.I):
                item('noFakeScrollbars', 'index.html', entry['tag'], entry['start'], entry['end'], '',
                     'Decorative scrollbar; native overflow scrolling already exists.')
    if checks.get('scrollContainers', {}).get('ok') is False:
        for node_id in checks['scrollContainers'].get('notScrollable', []):
            rule = '[data-node-id="%s"]{overflow:auto}\n' % node_id
            item('scrollContainers', 'styles.css', '[data-node-id="%s"]' % node_id, len(css_text), len(css_text), rule,
                 'Scene clip=scroll; use native overflow.', current='(append at end)')
        for node_id in checks['scrollContainers'].get('missing', []):
            if not restore(node_id, 'scrollContainers', 'Scroll container from the Scene is missing.'):
                manual.append('scrollContainers: [data-node-id="%s"] is missing and its parent was removed' % node_id)
    if checks.get('coverage', {}).get('ok') is False:
        details = _details(directory) or {}
        visible = set(details.get('expected', {}).get('visibleNodeIds', []))
        if document is not None:
            _, _, raw = select(document, frame_id or details.get('frameId'))
            visible = {node_id for node_id, node in raw.items() if visibility(node, raw) is not False}
        missing = [node_id for node_id in (pristine.nodes if pristine else []) if node_id in visible and node_id not in page.nodes]
        tops = [node_id for node_id in missing if pristine.nodes[node_id]['parent'] not in missing]
        restored = [node_id for node_id in tops if restore(node_id, 'coverage', 'Visible Scene node missing from the page.')]
        if not restored: manual.append('coverage: missing visible nodes %s cannot be re-anchored' % _cap(missing, 4))
    return result, items, manual


def _clip(text, limit=280):
    text = text.replace('\n', '\\n')
    return text if len(text) <= limit else text[:limit] + '... (%d chars)' % len(text)


def todo_text(directory, document=None, frame_id=None):
    result, items, manual = todo_items(directory, document, frame_id)
    status = 'verify: ' + ('PASS' if result['pass'] else 'FAIL ' + ', '.join(result['failures']))
    if not items and not manual:
        return status + '\nDONE: run report --dir DIR --scene SCENE, then append your notes to DIR/report.md.\n'
    lines = [status, '%d item(s). Apply one with: apply --dir DIR --item N (then run todo again).' % len(items)]
    for number, entry in enumerate(items[:8], 1):
        lines += ['ITEM %d [%s] %s %s: %s' % (number, entry['check'], entry['file'], entry['target'], entry['why']),
                  '  current: ' + _clip(entry['current']), '  replace: ' + (_clip(entry['replacement']) or '(delete)')]
    if len(items) > 8: lines.append('(+%d more after these)' % (len(items) - 8))
    lines += ['MANUAL ' + entry for entry in manual]
    return '\n'.join(lines) + '\n'


def apply_item(directory, number, document=None, frame_id=None):
    directory = Path(directory)
    _, items, _ = todo_items(directory, document, frame_id)
    if not 1 <= number <= len(items):
        raise ValueError('No todo item %d (there are %d); run todo again.' % (number, len(items)))
    entry = items[number - 1]
    path = directory / entry['file']
    text = path.read_text(encoding='utf-8')
    path.write_text(text[:entry['start']] + entry['replacement'] + text[entry['end']:], encoding='utf-8', newline='\n')
    _, remaining, manual = todo_items(directory, document, frame_id)
    return {'applied': number, 'check': entry['check'], 'file': entry['file'], 'target': entry['target'],
            'remainingItems': len(remaining), 'manual': len(manual),
            'next': 'todo --dir DIR' if remaining or manual else 'DONE: run report --dir DIR --scene SCENE'}


NOTES_HEADING = '## Notes on edits'


def write_report(directory, document=None, frame_id=None):
    """Deterministic report.md from the details file, verify and (optionally) the Scene; keeps existing notes."""
    directory = Path(directory)
    details = _details(directory)
    if details is None:
        raise ValueError('No scaffold-details.json in %s; run scaffold first.' % directory)
    result = verify(directory, document, frame_id)
    path = directory / 'report.md'
    notes = '_Implementer: append what you changed after the scaffold and which checks you ran._\n'
    if path.is_file():
        previous = path.read_text(encoding='utf-8')
        if NOTES_HEADING in previous: notes = previous.split(NOTES_HEADING, 1)[1].lstrip('\n')
    labels = details.get('labels', {})
    name = lambda node_id: '`%s`%s' % (node_id, ' "%s"' % labels[node_id] if labels.get(node_id) else '')
    profile = details.get('profile') or {}
    other = [frame for frame in details.get('frames', []) if frame['id'] != details['frameId']]
    out = ['# Reconstruction report', '',
           'Generated by `scene_reader.py report` from the Scene scaffold and the current site; '
           'only the last section is written by hand.', '',
           '## Source', '',
           '- Scene: `%s`' % details['sceneId'],
           '- Frame: `%s` (state `%s`), profile %sx%s' % (details['frameId'], details.get('frameState'),
                                                           _n(profile.get('width') or 0), _n(profile.get('height') or 0)),
           '- Frame choice: %s' % (details.get('frameChoice') or {}).get('reason', 'entry frame'),
           '- Other observed frames: %d%s' % (len(other), ' (default state: %s)' % ', '.join(
               '`%s`' % frame for frame in details['defaultFrames']) if details.get('defaultFrames') else ''),
           '- Verify: **%s**%s' % ('PASS' if result['pass'] else 'FAIL', '' if result['pass'] else ' (' + ', '.join(result['failures']) + ')'),
           '', '## Checks', '', '| Check | Result | Details |', '| --- | --- | --- |']
    for check, value in result['checks'].items():
        state = {True: 'pass', False: 'FAIL', None: 'skipped'}[value.get('ok')]
        extra = {key: item for key, item in value.items() if key != 'ok'}
        out.append('| %s | %s | %s |' % (check, state, json.dumps(extra, ensure_ascii=False)[:160].replace('|', '/') if extra else ''))
    out += ['', '## Interactions', '', 'Wired from measured transitions (%d):' % len(details['wired'])]
    out += ['- %s: %s on %s, shows %d / hides %d node(s)%s' % (
        item['transition'], item['mode'], name(item['trigger']), len(item['show']), len(item['hide']),
        ', aria ' + ', '.join('%s=%s' % (attribute, value) for _, attribute, value in item['aria']) if item['aria'] else '')
        for item in details['wired']] or ['- none']
    out += ['', 'Observed transitions not wired (%d):' % len(details['unwired'])]
    out += ['- %s: %s on %s, %s' % (item['transition'], item['action'], name(item['nodeId']) if item.get('nodeId') else 'no node',
                                     item['reason']) for item in details['unwired']] or ['- none']
    inert = details['actionsWithoutObservedOutcome']
    out += ['', 'Declared actions without an observed outcome: %d, left inert as `[data-unverified]`%s.' % (
        len(inert), ' on %d elements (e.g. %s)' % (len({item['nodeId'] for item in inert}), _cap([name(node_id) for node_id in dict.fromkeys(
            item['nodeId'] for item in inert)], 5)) if inert else '')]
    out += ['', '## Structure', '',
            '- Nodes: %d from the frame, %d injected from destination frames, %d style classes' % (
                details['nodeCount'], details['injectedNodes'], details['styleClasses']),
            '- Absolute-position fallback in %d parents (the Scene has no arrangement fact for them)' % len(details['absoluteFallbackParents']),
            '- Measured-geometry fallbacks (no sizing/margin facts): %d full-width, %d text widths, %d min-height, %d spacing, %d media sizes' % (
                len(details.get('widthFilledFromBounds', [])), len(details.get('textWidthFromBounds', [])), len(details.get('minHeightFromBounds', [])),
                len(details.get('spacingFromBounds', [])), len(details.get('mediaSizedFromBounds', []))),
            '- Paint order inferred (no fact): %d full-bleed media overlays drawn behind content%s' % (
                len(details.get('backgroundLayersInferred', [])), (' (%s)' % _cap(['`%s`' % node for node in details['backgroundLayersInferred']], 4))
                if details.get('backgroundLayersInferred') else ''),
            '- Component candidates (repeated sibling structures, `[data-component]`): %s' % (_cap(
                ['%s %dx under `%s`' % (item['id'], item['count'], item['parentId'] or '#scene-root')
                 for item in sorted(details['componentCandidates'], key=lambda item: -item['count'] * item['nodesPerInstance'])], 6) or 'none'),
            '', '## Evidence gaps (not fixed; need capture or a reference check)', '']
    gaps = []
    if details.get('visibleDialogs'):
        gaps.append('Frame shows dialog %s as captured; a default-state reference would come from %s.' % (
            _cap(['`%s`' % node for node in details['visibleDialogs']], 3), _cap(['`%s`' % frame for frame in details.get('defaultFrames') or []], 2) or 'another frame'))
    if details['missingFonts']: gaps.append('Missing font files: %s (generic fallback used).' % _cap(details['missingFonts'], 6))
    if details['missingMedia']:
        gaps.append('Missing media: %s.' % _cap(['`%s` (%s)' % (item['nodeId'], item.get('reason') or item.get('resourceId'))
                                                 for item in details['missingMedia']], 6))
    if details['unresolvedMasks']: gaps.append('Unresolved masks/clip paths: %s.' % _cap(['`%s`' % item['nodeId'] for item in details['unresolvedMasks']], 6))
    if details.get('paintOrderUnknownOverlapCount'):
        gaps.append('Paint order not captured for %d overlapping sibling pairs (e.g. %s); positioned placements are drawn above flow ones.' % (
            details['paintOrderUnknownOverlapCount'], _cap(['`%s`/`%s`' % tuple(pair) for pair in details['paintOrderUnknownOverlaps']], 3)))
    if details['stickyWithoutAnchors']: gaps.append('Sticky nodes without measured offsets: %d.' % len(details['stickyWithoutAnchors']))
    if details['unknownVisibility']: gaps.append('Nodes with unknown visibility rendered visible: %d.' % details['unknownVisibility'])
    if details.get('truncatedFrame'): gaps.append('The captured frame is truncated.')
    if details['assets']['unavailable']: gaps.append('Archived assets not available locally: %d.' % len(details['assets']['unavailable']))
    out += ['- ' + gap for gap in gaps] or ['- none recorded']
    out += ['', NOTES_HEADING, '', notes.rstrip('\n'), '']
    path.write_text('\n'.join(out), encoding='utf-8', newline='\n')
    return {'path': str(path), 'lines': len(out), 'verify': 'PASS' if result['pass'] else 'FAIL',
            'next': 'Append your own changes under "%s" in report.md, then stop.' % NOTES_HEADING}



def subtree_text(document, node_id, depth=2, frame_id=None, limit=80):
    _, frame, nodes = select(document, frame_id)
    if node_id not in nodes:
        raise ValueError('Unknown node ID in frame ' + frame['id'] + ': ' + node_id)
    kids = {}
    for node in frame['nodes']:
        kids.setdefault(node.get('parentId'), []).append(node['id'])
    lines, count = [], [0]

    def size(identifier):
        return 1 + sum(size(child) for child in kids.get(identifier, []))

    def walk(identifier, level):
        if len(lines) >= limit:
            count[0] += 1; return
        item = row(nodes[identifier], nodes)
        bits = [item['id'], item.get('role') or item['kind']]
        if item.get('name'): bits.append('name=' + json.dumps(item['name'], ensure_ascii=False))
        if item.get('text'): bits.append(json.dumps(item['text'][:80], ensure_ascii=False))
        if item.get('visible') is False: bits.append('[hidden]')
        if item.get('clip') == 'scroll': bits.append('[scroll]')
        for key in ('selected', 'expanded'):
            if key in item: bits.append('%s=%s' % (key, str(item[key]).lower()))
        if item.get('actions'): bits.append('{' + ' '.join(action['kind'] for action in item['actions']) + '}')
        children = kids.get(identifier, [])
        if children and level >= depth:
            bits.append('(+%d nodes)' % (size(identifier) - 1))
        lines.append('  ' * level + ' '.join(bits))
        if level < depth:
            for child in children: walk(child, level + 1)

    walk(node_id, 0)
    if count[0]: lines.append('... truncated at %d lines; raise --depth selectively on a child ID.' % limit)
    return 'frame ' + frame['id'] + '\n' + '\n'.join(lines) + '\n'



class _Page(HTMLParser):
    VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'source', 'track', 'wbr'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.elements, self.stack, self.hidden_by_node = [], [], {}

    def handle_starttag(self, tag, attrs, void=False):
        attrs = {key: value if value is not None else '' for key, value in attrs}
        hidden = 'hidden' in attrs or bool(self.stack and self.stack[-1][1])
        self.elements.append((tag, attrs))
        if 'data-node-id' in attrs: self.hidden_by_node.setdefault(attrs['data-node-id'], hidden)
        if not void and tag not in self.VOID: self.stack.append((tag, hidden))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs, void=True)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break


def _css_rules(text):
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
    rules = []
    for selectors, body in re.findall(r'([^{}]+)\{([^{}]*)\}', text):
        declarations = {}
        for part in body.split(';'):
            if ':' in part:
                key, value = part.split(':', 1)
                declarations[key.strip().lower()] = value.strip().lower()
        rules.append(([item.strip() for item in selectors.split(',')], declarations, body))
    return rules


def _matches(selector, tag, attrs):
    compound = re.split(r'[\s>+~]+', selector.strip())[-1]
    if not compound or ':' in compound.replace('\\:', ''):
        return False
    tokens = re.findall(r'(^[a-zA-Z][\w-]*|\*)|#([\w-]+)|\.([\w-]+)|\[([\w-]+)(?:=(?:"([^"]*)"|\'([^\']*)\'|([^\]]*)))?\]', compound)
    if not tokens:
        return False
    classes = attrs.get('class', '').split()
    for name, ident, cls, attr, v1, v2, v3 in tokens:
        if name and name != '*' and name.lower() != tag: return False
        if ident and attrs.get('id') != ident: return False
        if cls and cls not in classes: return False
        if attr:
            if attr not in attrs: return False
            value = v1 or v2 or v3
            if value and attrs[attr] != value.replace('\\"', '"').replace('\\\\', '\\'): return False
    return True


def verify(directory, document=None, frame_id=None, min_coverage=0.9):
    directory = Path(directory)
    root = directory.resolve()
    checks, failures = {}, []

    def check(name, ok, **details):
        checks[name] = {'ok': ok, **{key: value for key, value in details.items() if value not in (None, [], {})}}
        if ok is False: failures.append(name)

    index = directory / 'index.html'
    if not index.is_file():
        check('index', False, note='index.html missing')
        return {'schema': 'reconstruct.scaffold-verify.v1', 'pass': False, 'failures': failures, 'checks': checks}
    page = _Page()
    page.feed(index.read_text(encoding='utf-8', errors='replace'))
    elements = page.elements
    missing, remote, outside, stylesheets, scripts = [], [], [], [], []

    def resolve(reference, base, kind):
        reference = reference.strip()
        if not reference or reference.startswith(('#', 'data:', 'blob:', 'mailto:', 'tel:', 'javascript:')):
            return None
        if re.match(r'^(?:[a-z][a-z0-9+.-]*:)?//', reference, re.I):
            if kind != 'a': remote.append(reference[:120])
            return None
        target = (base / unquote(reference.split('#')[0].split('?')[0])).resolve()
        if root != target and root not in target.parents:
            outside.append(reference[:120])
        elif not target.exists():
            missing.append(reference[:120])
        return target

    for tag, attrs in elements:
        for key in ('src', 'href', 'poster'):
            if key in attrs:
                target = resolve(attrs[key], directory, tag if key == 'href' else 'resource')
                if tag == 'link' and 'stylesheet' in attrs.get('rel', '') and target and target.is_file(): stylesheets.append(target)
                if tag == 'script' and key == 'src' and target and target.is_file(): scripts.append(target)
        for candidate in attrs.get('srcset', '').split(','):
            if candidate.strip(): resolve(candidate.split()[0], directory, 'resource')
        for reference in re.findall(r'url\(\s*["\']?([^"\')]+)', attrs.get('style', '')):
            resolve(reference, directory, 'resource')
    rules = []
    for sheet in stylesheets:
        text = sheet.read_text(encoding='utf-8', errors='replace')
        for reference in re.findall(r'url\(\s*["\']?([^"\')]+)', text) + re.findall(r'@import\s+["\']([^"\']+)', text):
            resolve(reference, sheet.parent, 'resource')
        rules += _css_rules(text)
    check('localRefs', not missing and not outside and not remote, missing=missing[:8], outsideDir=outside[:8],
          remote=remote[:8], note=None if not remote else 'Remote resources fetch live origins; archive them locally.')

    node = shutil.which('node')
    if not scripts:
        check('scriptSyntax', None, note='no local scripts referenced')
    elif node is None:
        check('scriptSyntax', None, note='node not found; syntax check skipped')
    else:
        errors = []
        for script in scripts:
            result = subprocess.run([node, '--check', str(script)], capture_output=True, text=True, timeout=60)
            if result.returncode:
                lines = result.stderr.strip().splitlines() or ['?']
                line = next((item for item in lines if 'Error' in item), lines[-1])
                where = next((item.strip() for item in lines if script.name in item and ':' in item), '')
                errors.append({'file': script.name, 'error': (line.strip() + (' at line ' + where.split(script.name)[-1].lstrip(':').split(':')[0] if where else ''))[:200]})
        check('scriptSyntax', not errors, errors=errors[:4])

    def declarations(tag, attrs):
        found = {}
        for selectors, values, _ in rules:
            if any(_matches(selector, tag, attrs) for selector in selectors): found.update(values)
        for part in attrs.get('style', '').split(';'):
            if ':' in part:
                key, value = part.split(':', 1)
                found[key.strip().lower()] = value.strip().lower()
        return found

    overrides = [selector for selectors, values, _ in rules for selector in selectors
                 if '[hidden]' in selector and values.get('display', 'none').split('!')[0].strip() != 'none']
    guard = any('[hidden]' in selector and values.get('display', '').replace(' ', '') == 'none!important'
                for selectors, values, _ in rules for selector in selectors)
    hidden = [(tag, attrs) for tag, attrs in elements if 'hidden' in attrs]
    forced = [attrs.get('data-node-id') or tag for tag, attrs in hidden
              if '!important' in declarations(tag, attrs).get('display', '') and not guard]
    check('hiddenRespected', not overrides and not forced and (guard or not hidden), hiddenElements=len(hidden),
          overrides=overrides[:8], forcedVisible=forced[:8],
          note=None if guard or not hidden else 'Add [hidden]{display:none!important}.')

    fake = [attrs.get('data-node-id') or attrs.get('id') or attrs.get('class') or tag for tag, attrs in elements
            if attrs.get('role') == 'scrollbar' or re.search(r'scroll[-_]?(bar|thumb|track)', ' '.join(
                [attrs.get('class', ''), attrs.get('id', '')]), re.I)]
    check('noFakeScrollbars', not fake, elements=fake[:8])

    expected = None
    saved = _details(directory)
    if saved is None and (directory / 'scaffold-report.json').is_file():
        saved = json.loads((directory / 'scaffold-report.json').read_text(encoding='utf-8'))
        saved = saved if 'expected' in saved else None
    if document is not None and frame_id is None and saved is not None:
        frame_id = saved.get('frameId')
    if document is not None:
        scene, frame, raw = select(document, frame_id)
        visible = {node_id for node_id, item in raw.items() if visibility(item, raw) is not False}
        expected = {'frameId': frame['id'], 'visible': visible, 'hidden': set(raw) - visible,
                    'scroll': [node_id for node_id in visible if fact((raw[node_id].get('layout') or {}).get('clip')) == 'scroll']}
    elif saved is not None:
        expected = {'frameId': saved.get('frameId'), 'visible': set(saved['expected']['visibleNodeIds']),
                    'hidden': set(saved['expected'].get('hiddenNodeIds', [])),
                    'scroll': saved['expected']['scrollNodeIds']}
    by_node = {}
    for tag, attrs in elements:
        if 'data-node-id' in attrs: by_node.setdefault(attrs['data-node-id'], (tag, attrs))
    if expected is None:
        check('scrollContainers', None, note='pass --scene to check scroll containers')
        check('coverage', None, note='pass --scene to check node coverage')
        check('sceneHiddenRespected', None, note='pass --scene to check scene-hidden nodes')
    else:
        absent, clipped = [], []
        for node_id in expected['scroll']:
            if node_id not in by_node: absent.append(node_id); continue
            values = declarations(*by_node[node_id])
            if not any(values.get(key, '').split('!')[0].strip() in ('auto', 'scroll')
                       for key in ('overflow', 'overflow-y', 'overflow-x')):
                clipped.append(node_id)
        check('scrollContainers', not absent and not clipped, expected=len(expected['scroll']), missing=absent[:8],
              notScrollable=clipped[:8])
        covered = expected['visible'] & set(by_node)
        ratio = round(len(covered) / len(expected['visible']), 4) if expected['visible'] else 1.0
        shown = sorted(node_id for node_id in expected['hidden'] if page.hidden_by_node.get(node_id) is False)
        check('sceneHiddenRespected', not shown, sceneHidden=len(expected['hidden']), renderedVisible=shown[:8])
        check('coverage', ratio >= min_coverage, frameId=expected['frameId'], visibleNodes=len(expected['visible']),
              covered=len(covered), ratio=ratio, minimum=min_coverage,
              missing=sorted(expected['visible'] - covered)[:8])
    return {'schema': 'reconstruct.scaffold-verify.v1', 'pass': not failures, 'failures': failures, 'checks': checks}


HELP = """Workflow (one call per step; never read this script's source or the generated files to plan):
  1. scaffold --scene SCENE --output site [--frame ID]   complete page: markup, styles, assets, observed interactions
  2. todo --dir site                                      exact fixes for failed checks, or "DONE: run report"
  3. apply --dir site --item N                            apply todo item N, then run todo again
  4. report --dir site --scene SCENE                      writes site/report.md (frame, controls, components, gaps, checks)
  5. append your own changes under "## Notes on edits" in site/report.md, then stop
Evidence gaps (missing fonts/media, unknown paint order, unobserved destinations) are reported, never todo items.
Optional: verify --dir site (compact JSON, exit 1 on failure); tree --scene SCENE --id NODE --depth 2 (one subtree).
Queries: overview, brief, coverage, nodes --ids (exact facts, max 12), spec --output, assets --output.
Remote MCP: add --base-url DOWNLOAD_BASE_URL to scaffold/assets so assets download without credentials (SHA256-checked).
Scaffold also writes measured states as :hover/:focus-visible/:active/:disabled rules, captured themes as :root and
:root[data-theme=ID] tokens, breakpoints.css (@media blocks from measured widths) and layout-samples.json
(geometry at other widths, plus unplaced elements that exist only at a width).
scaffold-report.json is a short summary; scaffold-details.json and site/.scaffold/ are machine input, not for reading."""


def main():
    parser = argparse.ArgumentParser(description=__doc__, epilog=HELP, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=('scaffold', 'todo', 'apply', 'report', 'verify', 'tree', 'overview', 'brief',
                                            'coverage', 'nodes', 'spec', 'assets'))
    parser.add_argument('--scene', type=Path, help='MCP scene-export JSON (optional for todo/apply/report/verify)')
    parser.add_argument('--dir', type=Path, help='todo/apply/report/verify: scaffold output directory')
    parser.add_argument('--output', type=Path, help='scaffold/assets: directory; spec: file')
    parser.add_argument('--frame', help='exact frame ID (scaffold default: entryFrameId; later commands: the scaffold frame)')
    parser.add_argument('--base-url', help='scaffold/assets: download.baseUrl from the MCP tool result; fetches assets that are '
                                           'not on this machine (signed, no credentials) and verifies SHA256')
    parser.add_argument('--item', type=int, help='apply: todo item number')
    parser.add_argument('--id', help='tree: root node ID')
    parser.add_argument('--depth', type=int, default=2, help='tree: levels below --id (default 2)')
    parser.add_argument('--ids', nargs='+', default=[], help='nodes: up to 12 exact node IDs')
    parser.add_argument('--min-coverage', type=float, default=0.9, help='verify: visible-node coverage threshold')
    parser.add_argument('--offset', type=int, default=0)
    parser.add_argument('--limit', type=int, default=40, help='overview page size; tree line cap is 2x this (max 100)')
    args = parser.parse_args()
    if args.offset < 0 or not 1 <= args.limit <= 100 or len(args.ids) > 12 or not 0 <= args.depth <= 12:
        parser.error('Use offset >= 0, limit 1..100, depth 0..12 and at most 12 exact node IDs.')
    if args.command in ('todo', 'apply', 'report', 'verify'):
        if args.dir is None:
            parser.error(args.command + ' requires --dir DIRECTORY (a scaffold output)')
        document = json.loads(args.scene.read_text(encoding='utf-8')) if args.scene else None
        if args.command == 'todo':
            print(todo_text(args.dir, document, args.frame), end='')
            return
        if args.command == 'apply':
            if args.item is None:
                parser.error('apply requires --item N from todo')
            print(json.dumps(apply_item(args.dir, args.item, document, args.frame), ensure_ascii=False))
            return
        if args.command == 'report':
            print(json.dumps(write_report(args.dir, document, args.frame), ensure_ascii=False))
            return
        result = verify(args.dir, document, args.frame, args.min_coverage)
        print(json.dumps(result, ensure_ascii=False, separators=(',', ':')))
        raise SystemExit(0 if result['pass'] else 1)
    if args.scene is None:
        parser.error(args.command + ' requires --scene SCENE_EXPORT')
    document = json.loads(args.scene.read_text(encoding='utf-8'))
    select(document, args.frame)
    if args.command == 'overview':
        result = overview(document, args.frame, args.offset, args.limit)
    elif args.command == 'brief':
        print(readable_brief(document, args.frame), end='')
        return
    elif args.command == 'coverage':
        result = coverage(document)
    elif args.command == 'nodes':
        if not args.ids:
            parser.error('nodes requires --ids')
        result = exact_nodes(document, args.ids, args.frame)
    elif args.command == 'tree':
        if not args.id:
            parser.error('tree requires --id NODE')
        print(subtree_text(document, args.id, args.depth, args.frame, args.limit * 2), end='')
        return
    elif args.command == 'scaffold':
        if args.output is None:
            parser.error('scaffold requires --output DIRECTORY')
        result = scaffold(document, args.output, args.frame, args.base_url)
    elif args.command == 'spec':
        if args.output is None:
            parser.error('spec requires --output FILE so a dense frame is not dumped into context.')
        result = implementation_spec(document, args.frame)
        result['sourceSha256'] = hashlib.sha256(args.scene.read_bytes()).hexdigest()
        result['sourcePath'] = str(args.scene)
        args.output.write_text(json.dumps(result, ensure_ascii=False, separators=(',', ':')))
        print(json.dumps({'path': str(args.output), 'bytes': args.output.stat().st_size,
                          'frameId': result['frame']['id'], 'nodeCount': len(result['nodes']),
                          'styleCount': len(result['styles']), 'instruction': 'Use as local component input. Expand exact canonical facts only for unresolved provenance/layout questions; do not dump this entire file.'}))
        return
    else:
        if args.output is None:
            parser.error('assets requires --output DIRECTORY')
        result = copy_assets(document, args.output, args.base_url)
    print(json.dumps(result, ensure_ascii=False, separators=(',', ':')))


if __name__ == '__main__':
    main()
