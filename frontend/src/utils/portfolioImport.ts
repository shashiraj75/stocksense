import * as XLSX from "xlsx";

export interface ParsedHolding {
  symbol: string;
  qty: number;
  avgPrice: number;
  /** True if the symbol was corrected via company-name lookup (see below). */
  corrected?: boolean;
  originalSymbol?: string;
  /** True if the symbol couldn't be verified against our stock universe at
   * all (no company-name match, and the raw code isn't a known ticker) —
   * still imported, since it might just be missing from our universe, but
   * flagged so the user knows live prices/signals may not populate. */
  unverified?: boolean;
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
// Separate from SYMBOL_KEYS — some brokers (notably several Indian ones,
// e.g. ICICI Direct) export their own internal scrip code under "Symbol",
// which isn't the real NSE/BSE trading symbol at all (e.g. "EICMOT" for
// Eicher Motors, real ticker "EICHERMOT"). When a Company Name column is
// also present, it's used to cross-check/correct the symbol against our
// own stock universe rather than trusting the broker's code blindly.
const COMPANY_NAME_KEYS = ["companyname", "company", "description", "issuername"];

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

// Mirrors SearchBar's name normalization — corporate-suffix abbreviations
// and punctuation differ between broker exports and our own universe data
// ("Ltd" vs "Limited", "&" vs "and"), so exact string comparison alone
// misses real matches. Several broker exports (seen in practice from
// ICICI Direct) also hard-truncate long company names — sometimes by just
// 1-2 characters ("…LIMITE" missing the final D), sometimes much more
// aggressively ("…LT" missing "IMITED" entirely). Converting "Ltd" to
// "Limited" doesn't help when the truncation cuts mid-suffix, so the
// corporate suffix is stripped entirely from both sides instead of
// normalized to one spelling — comparing core business names only.
function normalizeCompanyName(s: string): string {
  return s
    .toLowerCase()
    .replace(/&/g, "and")
    .replace(/[.,()]/g, "")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/\b(private\s+)?(limited|limite|limit|limi|lim|ltd|lt|incorporated|inc|corporation|corp)\.?$/, "")
    .trim();
}

interface UniverseEntry { symbol: string; name: string; market: string }
let universeCache: UniverseEntry[] | null = null;
let universeLoadPromise: Promise<UniverseEntry[]> | null = null;

async function loadUniverse(): Promise<UniverseEntry[]> {
  if (universeCache) return universeCache;
  if (universeLoadPromise) return universeLoadPromise;
  universeLoadPromise = fetch("/stock_universe.json")
    .then(r => r.json())
    .then((data: { US: UniverseEntry[]; IN: UniverseEntry[] }) => {
      universeCache = [...data.IN, ...data.US];
      return universeCache;
    });
  return universeLoadPromise;
}

/** Finds the real trading symbol for a company name, scoped to one market. */
function resolveSymbolByName(companyName: string, market: "IN" | "US", universe: UniverseEntry[]): string | null {
  const target = normalizeCompanyName(companyName);
  const candidates = universe.filter(u => u.market === market);
  const exact = candidates.find(u => normalizeCompanyName(u.name) === target);
  if (exact) return exact.symbol;
  const partial = candidates.find(u => {
    const n = normalizeCompanyName(u.name);
    return n.includes(target) || target.includes(n);
  });
  return partial ? partial.symbol : null;
}

async function rowsToHoldings(rows: Record<string, unknown>[], market: "IN" | "US"): Promise<ImportParseResult> {
  const holdings: ParsedHolding[] = [];
  const errors: ImportRowError[] = [];
  if (rows.length === 0) return { holdings, errors };

  const headers = Object.keys(rows[0]);
  const symbolCol = findColumn(headers, SYMBOL_KEYS);
  const qtyCol = findColumn(headers, QTY_KEYS);
  const priceCol = findColumn(headers, PRICE_KEYS);
  const companyNameCol = findColumn(headers, COMPANY_NAME_KEYS);

  if (!symbolCol || !qtyCol || !priceCol) {
    errors.push({
      rowIndex: -1,
      raw: { headers },
      reason: `Couldn't find ${!symbolCol ? "a symbol" : !qtyCol ? "a quantity" : "an average price"} column. Found columns: ${headers.join(", ")}`,
    });
    return { holdings, errors };
  }

  const universe = await loadUniverse().catch(() => [] as UniverseEntry[]);
  const universeSymbols = new Set(universe.filter(u => u.market === market).map(u => u.symbol));

  rows.forEach((row, i) => {
    const symbolRaw = row[symbolCol];
    const qty = toNumber(row[qtyCol]);
    const price = toNumber(row[priceCol]);

    if (!symbolRaw || typeof symbolRaw !== "string" && typeof symbolRaw !== "number") {
      errors.push({ rowIndex: i, raw: row, reason: "Missing or invalid symbol" });
      return;
    }
    let symbol = String(symbolRaw).trim().toUpperCase().replace(/\.(NS|BO)$/, "");
    if (!symbol) {
      errors.push({ rowIndex: i, raw: row, reason: "Missing symbol" });
      return;
    }
    if (qty == null || qty <= 0) {
      errors.push({ rowIndex: i, raw: row, reason: `Invalid quantity (${row[qtyCol]})` });
      return;
    }
    // 0 is a legitimate average cost (bonus shares, gifted/transferred-in
    // holdings genuinely have no cost basis) — only reject negative or
    // unparseable values, not zero.
    if (price == null || price < 0) {
      errors.push({ rowIndex: i, raw: row, reason: `Invalid average price (${row[priceCol]})` });
      return;
    }

    let corrected = false;
    let originalSymbol: string | undefined;
    let unverified = false;

    if (!universeSymbols.has(symbol)) {
      const companyName = companyNameCol ? row[companyNameCol] : null;
      const resolved = companyName && typeof companyName === "string"
        ? resolveSymbolByName(companyName, market, universe)
        : null;
      if (resolved) {
        originalSymbol = symbol;
        symbol = resolved;
        corrected = true;
      } else {
        unverified = true;
      }
    }

    holdings.push({ symbol, qty, avgPrice: price, corrected, originalSymbol, unverified });
  });

  return { holdings, errors };
}

/** Parses an uploaded CSV/XLSX/XLS file (via SheetJS) into normalized holdings. */
export async function parseHoldingsFile(file: File, market: "IN" | "US" = "IN"): Promise<ImportParseResult> {
  const buf = await file.arrayBuffer();
  const workbook = XLSX.read(buf, { type: "array" });
  const sheet = workbook.Sheets[workbook.SheetNames[0]];
  const rows = XLSX.utils.sheet_to_json<Record<string, unknown>>(sheet, { defval: null });
  return rowsToHoldings(rows, market);
}

/**
 * Parses pasted text — handles tab-separated (copied from a spreadsheet),
 * comma-separated, or pipe-separated, with a header row.
 */
export async function parseHoldingsText(text: string, market: "IN" | "US" = "IN"): Promise<ImportParseResult> {
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

  return rowsToHoldings(rows, market);
}
