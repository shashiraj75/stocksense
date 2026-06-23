import * as XLSX from "xlsx";

export interface ExportableHolding {
  symbol: string;
  market: "IN" | "US";
  qty: number;
  avgPrice: number;
  curPrice: number | null;
  invested: number;
  current: number | null;
  plAmt: number | null;
  plPct: number | null;
  signal: string | null;
}

/**
 * Exports the full portfolio (both markets, whatever's currently loaded —
 * live prices included where available) to an .xlsx file the browser
 * downloads directly. Columns mirror what's already on screen, plus the
 * bare Symbol/Market/Qty/Avg Price subset the Import feature reads back in,
 * so a downloaded file can be round-tripped through Import later if needed.
 */
export function exportPortfolioToExcel(rows: ExportableHolding[]) {
  const data = rows.map(r => ({
    Symbol: r.symbol,
    Market: r.market,
    Qty: r.qty,
    "Avg Buy Price": r.avgPrice,
    "Current Price": r.curPrice ?? "",
    Invested: Math.round(r.invested),
    "Current Value": r.current != null ? Math.round(r.current) : "",
    "P&L": r.plAmt != null ? Math.round(r.plAmt) : "",
    "P&L %": r.plPct != null ? Number(r.plPct.toFixed(2)) : "",
    Signal: r.signal ?? "",
  }));

  const sheet = XLSX.utils.json_to_sheet(data);
  const workbook = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(workbook, sheet, "Portfolio");

  const today = new Date().toISOString().slice(0, 10);
  XLSX.writeFile(workbook, `stocksense360_portfolio_${today}.xlsx`);
}
