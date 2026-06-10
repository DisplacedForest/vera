import SwiftUI
import Charts

/// Native inline chart for a `vera:chart` block, themed to the app's dark palette + accent.
struct ChartBlockView: View {
    let spec: ChartSpec

    /// Series palette — leads with the app accent, then complementary hues.
    private static let palette: [Color] = [
        Theme.accent, Color(red: 0.40, green: 0.78, blue: 0.78), Color(red: 0.90, green: 0.62, blue: 0.30),
        Color(red: 0.72, green: 0.55, blue: 0.90), Color(red: 0.85, green: 0.45, blue: 0.50),
    ]
    private var seriesColors: [Color] {
        spec.series.enumerated().map { Self.palette[$0.offset % Self.palette.count] }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if !spec.title.isEmpty {
                Text(spec.title).font(.system(size: 13, weight: .semibold)).foregroundStyle(Theme.textPrimary)
            }
            Chart {
                ForEach(spec.series, id: \.name) { s in
                    ForEach(s.points, id: \.x) { p in
                        if spec.type == .line {
                            LineMark(x: .value(spec.xLabel.isEmpty ? "x" : spec.xLabel, p.x),
                                     y: .value(spec.yLabel.isEmpty ? "y" : spec.yLabel, p.y))
                                .foregroundStyle(by: .value("series", s.name))
                                .symbol(by: .value("series", s.name))
                                .interpolationMethod(.catmullRom)
                        } else {
                            BarMark(x: .value(spec.xLabel.isEmpty ? "x" : spec.xLabel, p.x),
                                    y: .value(spec.yLabel.isEmpty ? "y" : spec.yLabel, p.y))
                                .foregroundStyle(by: .value("series", s.name))
                                .position(by: .value("series", s.name))
                        }
                    }
                }
            }
            .chartForegroundStyleScale(range: seriesColors)
            .chartLegend(spec.series.count > 1 ? .visible : .hidden)
            .chartXAxis { AxisMarks { AxisValueLabel().foregroundStyle(Theme.textSecondary) } }
            .chartYAxis {
                AxisMarks { AxisGridLine().foregroundStyle(Theme.hairline)
                    AxisValueLabel().foregroundStyle(Theme.textSecondary) }
            }
            .frame(height: 220)
        }
        .padding(12)
        .background(Theme.surface).clipShape(RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(Theme.hairline, lineWidth: 1))
    }
}

/// A row of big-number stat cards for a `vera:stats` block.
struct StatCardsView: View {
    let cards: [StatCard]
    var body: some View {
        HStack(spacing: 10) {
            ForEach(cards) { c in
                VStack(alignment: .leading, spacing: 3) {
                    Text(c.value).font(.system(size: 22, weight: .bold)).foregroundStyle(Theme.textPrimary)
                    Text(c.label).font(.system(size: 11, weight: .medium)).foregroundStyle(Theme.textSecondary)
                    if let sub = c.sub, !sub.isEmpty {
                        Text(sub).font(.system(size: 10)).foregroundStyle(Theme.textSecondary.opacity(0.8))
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(12)
                .background(Theme.surface).clipShape(RoundedRectangle(cornerRadius: 12))
                .overlay(RoundedRectangle(cornerRadius: 12).stroke(Theme.hairline, lineWidth: 1))
            }
        }
    }
}
