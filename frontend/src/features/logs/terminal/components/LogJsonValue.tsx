import type { ReactNode } from 'react';

function parseEmbeddedJson(value: string): unknown {
  const trimmed = value.trim();
  const looksLikeJson =
    (trimmed.startsWith('{') && trimmed.endsWith('}')) ||
    (trimmed.startsWith('[') && trimmed.endsWith(']'));
  if (!looksLikeJson) return value;

  try {
    return JSON.parse(trimmed);
  } catch {
    return value;
  }
}

function findJsonFragmentEnd(value: string, start: number): number | null {
  const opening = value[start];
  if (opening !== '{' && opening !== '[') return null;

  const stack: string[] = [opening];
  let inString = false;
  let escaped = false;
  for (let index = start + 1; index < value.length; index++) {
    const char = value[index];
    if (inString) {
      if (escaped) escaped = false;
      else if (char === '\\') escaped = true;
      else if (char === '"') inString = false;
      continue;
    }
    if (char === '"') {
      inString = true;
      continue;
    }
    if (char === '{' || char === '[') {
      stack.push(char);
      continue;
    }
    if (char !== '}' && char !== ']') continue;

    const expected = char === '}' ? '{' : '[';
    if (stack.at(-1) !== expected) return null;
    stack.pop();
    if (stack.length === 0) return index;
  }
  return null;
}

function splitEmbeddedJson(value: string): Array<string | Record<string, unknown> | unknown[]> | null {
  const segments: Array<string | Record<string, unknown> | unknown[]> = [];
  let textStart = 0;
  let foundJson = false;

  for (let index = 0; index < value.length; index++) {
    if (value[index] !== '{' && value[index] !== '[') continue;
    const previous = index > 0 ? value[index - 1] : '';
    if (previous && !/[\s:=,(]/.test(previous)) continue;
    const end = findJsonFragmentEnd(value, index);
    if (end === null) break;

    const candidate = value.slice(index, end + 1);
    try {
      const parsed = JSON.parse(candidate);
      if (!parsed || typeof parsed !== 'object') continue;
      if (index > textStart) segments.push(value.slice(textStart, index));
      segments.push(parsed as Record<string, unknown> | unknown[]);
      foundJson = true;
      index = end;
      textStart = end + 1;
    } catch {
      // Braces used as regular text are kept untouched.
    }
  }

  if (!foundJson) return null;
  if (textStart < value.length) segments.push(value.slice(textStart));
  return segments;
}

export function LogJsonValue({ value, depth = 0 }: { value: unknown; depth?: number }) {
  const normalized = typeof value === 'string' ? parseEmbeddedJson(value) : value;
  const parsedFromString = normalized !== value;

  if (depth >= 8 && normalized && typeof normalized === 'object') {
    return <span className="log-json-string">{JSON.stringify(normalized, null, 2)}</span>;
  }

  let content: ReactNode;
  if (Array.isArray(normalized)) {
    content = normalized.length > 0 ? (
      <div className="log-json-collection">
        {normalized.map((item, index) => (
          <div key={index} className="log-json-entry">
            <span className="log-json-key">[{index}]</span>
            <div className="log-json-value">
              <LogJsonValue value={item} depth={depth + 1} />
            </div>
          </div>
        ))}
      </div>
    ) : <span className="log-faint-text">[]</span>;
  } else if (normalized && typeof normalized === 'object') {
    const entries = Object.entries(normalized as Record<string, unknown>);
    content = entries.length > 0 ? (
      <div className="log-json-collection">
        {entries.map(([key, item]) => (
          <div key={key} className="log-json-entry">
            <span className="log-json-key">{key}</span>
            <div className="log-json-value">
              <LogJsonValue value={item} depth={depth + 1} />
            </div>
          </div>
        ))}
      </div>
    ) : <span className="log-faint-text">{'{}'}</span>;
  } else if (normalized === null || normalized === undefined) {
    content = <span className="log-faint-text">{String(normalized)}</span>;
  } else if (typeof normalized === 'boolean') {
    content = <span className="log-warning-text">{String(normalized)}</span>;
  } else if (typeof normalized === 'number') {
    content = <span className="log-success-text">{String(normalized)}</span>;
  } else {
    const stringValue = String(normalized);
    const segments = splitEmbeddedJson(stringValue);
    content = segments ? (
      <div className="log-json-mixed">
        {segments.map((segment, index) => (
          typeof segment === 'string' ? (
            <span key={index} className="log-json-string">{segment}</span>
          ) : (
            <div key={index} className="log-json-embedded">
              <span className="log-json-badge">JSON</span>
              <LogJsonValue value={segment} depth={depth + 1} />
            </div>
          )
        ))}
      </div>
    ) : <span className="log-json-string">{stringValue}</span>;
  }

  if (!parsedFromString) return content;
  return (
    <div className="log-json-embedded">
      <span className="log-json-badge">JSON</span>
      {content}
    </div>
  );
}
