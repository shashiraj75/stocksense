import { NewsArticle } from "@/utils/api";
import clsx from "clsx";
import { formatDistanceToNow } from "date-fns";

const SENTIMENT_STYLE: Record<string, string> = {
  BULLISH: "text-bull",
  BEARISH: "text-bear",
  NEUTRAL: "text-gray-400",
};

export function NewsCard({ article }: { article: NewsArticle }) {
  const label = article.sentiment?.label ?? "NEUTRAL";
  const age = formatDistanceToNow(new Date(article.published_at), { addSuffix: true });

  return (
    <a
      href={article.url}
      target="_blank"
      rel="noopener noreferrer"
      className="block p-4 rounded-xl bg-dark-card border border-dark-border hover:border-brand-500/50 transition-colors"
    >
      <div className="flex items-start justify-between gap-3">
        <p className="text-sm text-white leading-snug line-clamp-2">{article.title}</p>
        <span className={clsx("text-xs font-bold shrink-0", SENTIMENT_STYLE[label])}>
          {label}
        </span>
      </div>
      <div className="mt-2 flex items-center gap-2 text-xs text-gray-500">
        <span>{article.source}</span>
        <span>•</span>
        <span>{age}</span>
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
