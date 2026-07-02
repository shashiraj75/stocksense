import { NewsArticle } from "@/utils/api";
import { parseArticleDate } from "@/utils/newsDisplay";
import clsx from "clsx";
import { formatDistanceToNow } from "date-fns";

const SENTIMENT_STYLE: Record<string, string> = {
  BULLISH: "text-bull",
  BEARISH: "text-bear",
  NEUTRAL: "text-gray-400",
};

export function NewsCard({ article }: { article: NewsArticle }) {
  const label = article.sentiment?.label ?? "NEUTRAL";
  // Backend is the single source of truth for eligibility — never re-derive
  // it from ages here. undefined = payload predates the annotation (e.g. a
  // cached older response); make no inclusion/exclusion claim in that case.
  const eligible = article.sentiment_eligible;
  const isHistorical = eligible === false;

  // Crash-safe date rendering: date-fns throws RangeError on invalid dates,
  // and "Invalid Date" text must never reach the user.
  const published = parseArticleDate(article.published_at);
  const age = published ? formatDistanceToNow(published, { addSuffix: true }) : null;

  return (
    <a
      href={article.url}
      target="_blank"
      rel="noopener noreferrer"
      className="block p-4 rounded-xl bg-dark-card border border-dark-border hover:border-brand-500/50 transition-colors"
    >
      {/* Eligibility label first — visible text, not color alone, carries
          the current-vs-historical meaning (accessibility requirement). */}
      {eligible === true && (
        <p className="text-[10px] text-gray-500 mb-1">Included in current sentiment</p>
      )}
      {isHistorical && (
        <p className="text-[10px] text-gray-500 mb-1">
          Historical context — not used in current sentiment
        </p>
      )}
      <div className="flex items-start justify-between gap-3">
        <p className="text-sm text-white leading-snug line-clamp-2">{article.title}</p>
        <span
          className={clsx(
            "text-xs font-bold shrink-0",
            SENTIMENT_STYLE[label],
            // On historical articles the chip is a historical classification,
            // visually secondary to the context label above it.
            isHistorical && "opacity-60",
          )}
        >
          {label}
        </span>
      </div>
      <div className="mt-2 flex items-center gap-2 text-xs text-gray-500">
        <span>{article.source}</span>
        <span>•</span>
        <span>{age ?? "publication date unavailable"}</span>
        {article.sentiment && (
          <>
            <span>•</span>
            <span>{Math.round(article.sentiment.score * 100)}% conf</span>
          </>
        )}
      </div>
    </a>
  );
}
