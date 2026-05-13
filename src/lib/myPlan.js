import myPlanText from '../../myplan.md?raw';

function splitMarkdownRow(line) {
  return line
    .split('|')
    .map((part) => part.trim())
    .filter((part) => part.length > 0);
}

function parseMetadata(lines) {
  const metadata = [];

  lines.forEach((line) => {
    if (!line.startsWith('- ')) {
      return;
    }

    const content = line.slice(2);
    const separator = content.indexOf(':');
    if (separator < 0) {
      return;
    }

    const key = content.slice(0, separator).trim();
    const value = content.slice(separator + 1).trim();
    metadata.push({ key, value });
  });

  return metadata;
}

function parseSummary(lines) {
  const summary = [];

  lines.forEach((line) => {
    const matched = line.match(/^\*\*(.+?):\s*(.+)\*\*$/);
    if (!matched) {
      return;
    }
    summary.push({ key: matched[1].trim(), value: matched[2].trim() });
  });

  return summary;
}

function parseTable(lines) {
  const tableStart = lines.findIndex((line) => line.trim().startsWith('|'));
  if (tableStart < 0 || tableStart + 2 >= lines.length) {
    return { headers: [], rows: [] };
  }

  const headers = splitMarkdownRow(lines[tableStart]);
  const rows = [];

  for (let i = tableStart + 2; i < lines.length; i += 1) {
    const line = lines[i].trim();
    if (!line.startsWith('|')) {
      break;
    }

    const values = splitMarkdownRow(line);
    const row = {};

    headers.forEach((header, index) => {
      row[header] = values[index] || '';
    });

    rows.push(row);
  }

  return { headers, rows };
}

export async function loadMyPlan() {
  const lines = myPlanText.split(/\r?\n/).map((line) => line.trim());

  return {
    title: lines.find((line) => line.startsWith('# '))?.replace(/^#\s*/, '') || 'MyPlan',
    metadata: parseMetadata(lines),
    table: parseTable(lines),
    summary: parseSummary(lines),
  };
}
