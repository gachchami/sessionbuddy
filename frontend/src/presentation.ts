export function formatRecommendationChoice(value: string): string {
  const spaced = value.replace(/_/g, " ").trim();
  return spaced ? `${spaced.charAt(0).toUpperCase()}${spaced.slice(1)}` : value;
}
