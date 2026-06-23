"use client";
import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api } from "@/utils/api";
import { useAuth } from "@/lib/AuthContext";
import { X } from "lucide-react";
import clsx from "clsx";

export function NpsPopup() {
  const { user } = useAuth();
  const [score, setScore] = useState<number | null>(null);
  const [comment, setComment] = useState("");
  const [dismissed, setDismissed] = useState(false);

  const { data: due } = useQuery({
    queryKey: ["nps-due", user?.id],
    queryFn: async () => {
      if (!user?.id) return { due: false };
      const res = await api.get("/api/feedback/nps/due", { params: { user_id: user.id } });
      return res.data as { due: boolean };
    },
    enabled: !!user?.id,
    staleTime: 60 * 60 * 1000, // check once per session
  });

  const submitMutation = useMutation({
    mutationFn: async () => {
      if (score === null || !user?.id) return;
      await api.post("/api/feedback/nps", {
        user_id: user.id,
        score,
        comment: comment.trim() || null,
      });
    },
    onSuccess: () => setDismissed(true),
  });

  if (!due?.due || dismissed) return null;

  const submitted = submitMutation.isSuccess;

  return (
    <div className="fixed bottom-6 right-6 z-50 w-[320px] max-w-[calc(100vw-3rem)] rounded-2xl border border-white/10 bg-dark-card shadow-2xl p-5">
      <button
        onClick={() => setDismissed(true)}
        className="absolute top-3 right-3 text-gray-500 hover:text-white"
      >
        <X size={14} />
      </button>

      {submitted ? (
        <div className="text-center py-2">
          <p className="text-2xl mb-1">🙏</p>
          <p className="text-sm font-semibold text-white">Thank you for your feedback!</p>
          <p className="text-xs text-gray-500 mt-1">It helps us improve StockSense.</p>
        </div>
      ) : (
        <>
          <p className="text-xs text-gray-400 mb-0.5">Quick feedback</p>
          <p className="text-sm font-semibold text-white mb-3">
            How likely are you to recommend StockSense to a friend?
          </p>

          <div className="flex gap-1 mb-1">
            {Array.from({ length: 11 }, (_, i) => (
              <button
                key={i}
                onClick={() => setScore(i)}
                className={clsx(
                  "flex-1 aspect-square rounded text-[10px] font-bold border transition-all",
                  score === i
                    ? i >= 9 ? "bg-bull/30 border-bull text-bull"
                      : i >= 7 ? "bg-yellow-500/30 border-yellow-500 text-yellow-400"
                      : "bg-bear/30 border-bear text-red-400"
                    : "bg-white/5 border-white/10 text-gray-500 hover:text-white hover:border-white/20"
                )}
              >
                {i}
              </button>
            ))}
          </div>
          <div className="flex justify-between text-[11px] text-gray-400 mb-3">
            <span>Not likely</span>
            <span>Very likely</span>
          </div>

          <textarea
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            placeholder="Any comments? (optional)"
            rows={2}
            className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-xs text-white placeholder-gray-600 resize-none focus:outline-none focus:border-white/20 mb-3"
          />

          <button
            onClick={() => submitMutation.mutate()}
            disabled={score === null || submitMutation.isPending}
            className="w-full py-2 rounded-lg text-xs font-semibold bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 disabled:cursor-not-allowed text-white transition-all"
          >
            {submitMutation.isPending ? "Submitting…" : "Submit"}
          </button>
        </>
      )}
    </div>
  );
}
