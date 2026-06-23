import * as XLSX from "xlsx";

export interface ParsedHolding {
  symbol: string;
  qty: number;
  avgPrice: number;
}

export interface ImportRowError {
  rowIndex: number;
  raw: Record<string, unknown>;
  reason: string;
}

export interface ImportParseResult {
  holdings: ParsedHolding[];
  errors: ImportRowError[];
}

// Most broker exports use one of these names for each field — matched
// case-insensitively, ignoring spaces/punctuation, so "Avg. Cost Price",
// "avgCost", and "Average Price" all resolve to the same field.
const SYMBOL_KEYS = ["symbol", "tradingsymbol", "instrument", "scripname", "scrip", "stock", "name", "stockname"];
const QTY_KEYS = ["qty", "quantity", "shares", "units", "holdingqty"];
const PRICE_KEYS = ["avgprice", "averageprice", "avgcost", "avgcostprice", "buyprice", "costprice", "averagecost", "avg"];

function normalizeKey(k: string): string {
  return k.toLowerCase().replace(/[^a-z0-9]/g, "");
}

function findColumn(headers: string[], candidates: string[]): string | null {
  const normalized = headers.map(h => ({ original: h, norm: normalizeKey(h) }));
  for (const candidate of candidates) {
    const match = normalized.find(h => h.norm === candidate);
    if (match) return match.original;
  }
  // Fallback: partial match (e.g. a header like "NSE Symbol" contains "symbol")
  for (const candidate of candidates) {
    const match = normalized.find(h => h.norm.includes(candidate));
    if (match) return match.original;
  }
  return null;
}

function toNumber(v: unknown): number | null {
  if (v == null) return null;
  if (typeof v === "number") return isFinite(v) ? v : null;
  const cleaned = String(v).replace(/[,₹$\s]/g, "");
  const n = parseFloat(cleaned);
  return isNaN(n) ? null : n;
}

function rowsToHoldings(rows: Record<string, unknown>[]): ImportParseResult {
  const holdings: ParsedHolding[] = [];
  const errors: ImportRowError[] = [];
  if (rows.length === 0) return { holdings, errors };

  const headers = Object.keys(rows[0]);
  const symbolCol = findColumn(headers, SYMBOL_KEYS);
  const qtyCol = findColumn(headers, QTY_KEYS);
  const priceCol = findColumn(headers, PRICE_KEYS);

  if (!symbolCol || !qtyCol || !priceCol) {
    errors.push({
      rowIndex: -1,
      raw: { headers },
      reason: `Couldn't find ${!symbolCol ? "a symbol" : !qtyCol ? "a quantity" : "an average price"} column. Found columns: ${headers.join(", ")}`,
    });
    return { holdings, errors };
  }

  rows.forEach((row, i) => {
    const symbolRaw = row[symbolCol];
    const qty = toNumber(row[qtyCol]);
    const price = toNumber(row[priceCol]);

    if (!symbolRaw || typeof symbolRaw !== "string" && typeof symbolRaw !== "number") {
      errors.push({ rowIndex: i, raw: row, reason: "Missing or invalid symbol" });
      return;
    }
    const symbol = String(symbolRaw).trim().toUpperCase().replace(/\.(NS|BO)$/, "");
    if (!symbol) {
      errors.push({ rowIndex: i, raw: row, reason: "Missing symbol" });
      return;
    }
    if (qty == null || qty <= 0) {
      errors.push({ rowIndex: i, raw: row, reason: `Invalid quantity (${row[qtyCol]})` });
      return;
    }
    if (price == null || price <= 0) {
      errors.push({ rowIndex: i, raw: row, reason: `Invalid average price (${row[priceCol]})` });
      return;
    }
    holdings.push({ symbol, qty, avgPrice: price });
  });

  return { holdings, errors };
}

/** Parses an uploaded CSV/XLSX/XLS file (via SheetJS) into normalized holdings. */
export async function parseHoldingsFile(file: File): Promise<ImportParseResult> {
  const buf = await file.arrayBuffer();
  const workbook = XLSX.read(buf, { type: "array" });
  const sheet = workbook.Sheets[workbook.SheetNames[0]];
  const rows = XLSX.utils.sheet_to_json<Record<string, unknown>>(sheet, { defval: null });
  return rowsToHoldings(rows);
}

/**
 * Parses pasted text — handles tab-separated (copied from a spreadsheet),
 * comma-separated, or pipe-separated, with a header row.
 */
export function parseHoldingsText(text: string): ImportParseResult {
  const lines = text.trim().split(/\r?\n/).filter(l => l.trim().length > 0);
  if (lines.length < 2) {
    return { holdings: [], errors: [{ rowIndex: -1, raw: {}, reason: "Need a header row plus at least one data row" }] };
  }

  const delimiter = lines[0].includes("\t") ? "\t" : lines[0].includes("|") ? "|" : ",";
  const headers = lines[0].split(delimiter).map(h => h.trim());
  const rows: Record<string, unknown>[] = lines.slice(1).map(line => {
    const cells = line.split(delimiter).map(c => c.trim());
    const row: Record<string, unknown> = {};
    headers.forEach((h, i) => { row[h] = cells[i] ?? null; });
    return row;
  });

  return rowsToHoldings(rows);
}
