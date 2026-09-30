"""JSON with comments, and value edits that retain comments and formatting.

Only //, /* */ and trailing commas extend standard JSON. Offsets in the cleaned
document remain identical to the source, allowing edits of individual values.
"""
import json


def clean(text, *, trailing_commas=True):
    chars = list(text)
    i = 0
    while i < len(text):
        if text[i] == '"':
            i += 1
            while i < len(text):
                if text[i] == '\\':
                    i += 2
                elif text[i] == '"':
                    i += 1
                    break
                else:
                    i += 1
        elif text.startswith('//', i):
            end = i + 2
            while end < len(text) and text[end] not in '\r\n':
                end += 1
            chars[i:end] = ' ' * (end - i)
            i = end
        elif text.startswith('/*', i):
            end = text.find('*/', i + 2)
            if end < 0:
                raise ValueError('Unterminated JSONC block comment')
            end += 2
            chars[i:end] = [c if c in '\r\n' else ' ' for c in text[i:end]]
            i = end
        else:
            i += 1
    if trailing_commas:
        source = ''.join(chars)
        i = 0
        while i < len(source):
            if source[i] == '"':
                i += 1
                while i < len(source):
                    if source[i] == '\\':
                        i += 2
                    elif source[i] == '"':
                        i += 1
                        break
                    else:
                        i += 1
            elif source[i] == ',':
                after = i + 1
                before = i - 1
                while after < len(source) and source[after].isspace():
                    after += 1
                while before >= 0 and source[before].isspace():
                    before -= 1
                if (after < len(source) and source[after] in '}]' and before >= 0
                        and source[before] not in '{[:,'):
                    chars[i] = ' '
                i += 1
            else:
                i += 1
    return ''.join(chars)


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'Duplicate JSONC key: {key}')
        result[key] = value
    return result


def loads(text):
    return json.loads(clean(text), object_pairs_hook=_unique_pairs)


def update(text, values):
    """Replace changed values/add missing keys without rewriting existing text."""
    current = loads(text)
    if not isinstance(current, dict) or not isinstance(values, dict):
        raise ValueError('JSONC configuration must be an object')
    source = clean(text)
    comments_only = clean(text, trailing_commas=False)
    decoder = json.JSONDecoder()
    edits = []

    def skip(pos):
        while pos < len(source) and source[pos].isspace():
            pos += 1
        return pos

    def object_edit(start, desired):
        pos = skip(start + 1)
        fields = {}
        while source[pos] != '}':
            key_start = pos
            key, end = decoder.raw_decode(source, pos)
            pos = skip(end)
            if source[pos] != ':':
                raise ValueError('Missing JSONC property colon')
            value_start = skip(pos + 1)
            value, value_end = decoder.raw_decode(source, value_start)
            fields[key] = (key_start, value_start, value_end, value)
            pos = skip(value_end)
            if source[pos] == ',':
                pos = skip(pos + 1)
        closing = pos
        missing = {}
        for key, value in desired.items():
            if key not in fields:
                missing[key] = value
                continue
            _, first, last, old = fields[key]
            if isinstance(old, dict) and isinstance(value, dict):
                object_edit(first, value)
            elif old != value:
                edits.append((first, last, json.dumps(value, ensure_ascii=False)))
        if not missing:
            return
        if fields:
            last_end = list(fields.values())[-1][2]
            if ',' not in comments_only[last_end:closing]:
                edits.append((last_end, last_end, ','))
        line_start = text.rfind('\n', 0, closing) + 1
        closing_indent = text[line_start:closing]
        if closing_indent.strip():
            opening_prefix = text[text.rfind('\n', 0, start) + 1:start]
            closing_indent = opening_prefix[:len(opening_prefix) - len(opening_prefix.lstrip())]
            insertion = closing
            prefix = '\n'
        else:
            insertion = line_start
            prefix = '' if insertion and text[insertion - 1] == '\n' else '\n'
        indent = closing_indent + '  '
        if fields:
            key_start = next(iter(fields.values()))[0]
            existing_indent = text[text.rfind('\n', 0, key_start) + 1:key_start]
            if not existing_indent.strip() and len(existing_indent) > len(closing_indent):
                indent = existing_indent
        additions = ',\n'.join(indent + json.dumps(key, ensure_ascii=False) + ': '
                               + json.dumps(value, ensure_ascii=False) for key, value in missing.items())
        suffix = '\n' + (closing_indent if insertion == closing else '')
        edits.append((insertion, insertion, prefix + additions + suffix))

    object_edit(skip(0), values)
    for _, (start, end, replacement) in sorted(enumerate(edits),
                                             key=lambda item: (item[1][0], item[0]), reverse=True):
        text = text[:start] + replacement + text[end:]
    # Validate the result before a caller can publish it.
    def merged(old, new):
        result = dict(old)
        for key, value in new.items():
            result[key] = merged(old[key], value) if isinstance(old.get(key), dict) and isinstance(value, dict) else value
        return result
    if loads(text) != merged(current, values):
        raise ValueError('JSONC edit did not produce the requested configuration')
    return text
