"use client";
import { useEffect, useRef } from "react";
import { createChart, ColorType, IChartApi, CandlestickData } from "lightweight-charts";
import { OHLCVBar } from "@/utils/api";

interface Props {
  data: OHLCVBar[];
  height?: number;
}

export function PriceChart({ data, height = 400 }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

  useEffect(() => {
    if (!containerRef.current || !data.length) return;

    const chart = createChart(containerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: "#1a1d2e" },
        textColor: "#9ca3af",
      },
      grid: {
        vertLines: { color: "#2a2d3e" },
        horzLines: { color: "#2a2d3e" },
      },
      width: containerRef.current.clientWidth,
      height,
      timeScale: { borderColor: "#2a2d3e" },
      rightPriceScale: { borderColor: "#2a2d3e" },
    });
    chartRef.current = chart;

    const candleSeries = chart.addCandlestickSeries({
      upColor: "#22c55e",
      downColor: "#ef4444",
      borderUpColor: "#22c55e",
      borderDownColor: "#ef4444",
      wickUpColor: "#22c55e",
      wickDownColor: "#ef4444",
    });

    const formatted: CandlestickData[] = data.map((d) => ({
      time: d.date as any,
      open: d.open,
      high: d.high,
      low: d.low,
      close: d.close,
    }));
    candleSeries.setData(formatted);
    chart.timeScale().fitContent();

    const handleResize = () => {
      if (containerRef.current) chart.applyOptions({ width: containerRef.current.clientWidth });
    };
    window.addEventListener("resize", handleResize);

    return () => {
      window.removeEventListener("resize", handleResize);
      chart.remove();
    };
  }, [data, height]);

  return <div ref={containerRef} className="w-full rounded-xl overflow-hidden" />;
}
