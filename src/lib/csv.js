function splitCsvLine(line) {
  const cells = [];
  let current = '';
  let inQuotes = false;

  for (let i = 0; i < line.length; i += 1) {
    const char = line[i];
    const next = line[i + 1];

    if (char === '"') {
      if (inQuotes && next === '"') {
        current += '"';
        i += 1;
      } else {
        inQuotes = !inQuotes;
      }
      continue;
    }

    if (char === ',' && !inQuotes) {
      cells.push(current);
      current = '';
      continue;
    }

    current += char;
  }

  cells.push(current);
  return cells;
}

export function parseCsv(text) {
  const raw = (text || '').replace(/^\uFEFF/, '').trim();
  if (!raw) {
    return [];
  }

  const lines = raw.split(/\r?\n/).filter((line) => line.length > 0);
  if (lines.length <= 1) {
    return [];
  }

  const headers = splitCsvLine(lines[0]).map((item) => item.trim());

  return lines.slice(1).map((line) => {
    const values = splitCsvLine(line);
    const row = {};

    headers.forEach((header, index) => {
      row[header] = (values[index] || '').trim();
    });

    return row;
  });
}
