export type ImageSizePreset = {
  ratio: string;
  tier: "1k" | "2k" | "4k";
  width: string;
  height: string;
};

export const IMAGE_SIZE_PRESETS: ImageSizePreset[] = [
  { ratio: "1:1", tier: "1k", width: "1024", height: "1024" },
  { ratio: "1:1", tier: "2k", width: "2048", height: "2048" },
  { ratio: "1:1", tier: "4k", width: "2880", height: "2880" },
  { ratio: "3:2", tier: "1k", width: "1536", height: "1024" },
  { ratio: "3:2", tier: "2k", width: "2160", height: "1440" },
  { ratio: "3:2", tier: "4k", width: "3456", height: "2304" },
  { ratio: "2:3", tier: "1k", width: "1024", height: "1536" },
  { ratio: "2:3", tier: "2k", width: "1440", height: "2160" },
  { ratio: "2:3", tier: "4k", width: "2304", height: "3456" },
  { ratio: "16:9", tier: "1k", width: "1280", height: "720" },
  { ratio: "16:9", tier: "2k", width: "2560", height: "1440" },
  { ratio: "16:9", tier: "4k", width: "3840", height: "2160" },
  { ratio: "9:16", tier: "1k", width: "720", height: "1280" },
  { ratio: "9:16", tier: "2k", width: "1440", height: "2560" },
  { ratio: "9:16", tier: "4k", width: "2160", height: "3840" },
  { ratio: "4:3", tier: "1k", width: "1024", height: "768" },
  { ratio: "4:3", tier: "2k", width: "2048", height: "1536" },
  { ratio: "4:3", tier: "4k", width: "3200", height: "2400" },
  { ratio: "3:4", tier: "1k", width: "768", height: "1024" },
  { ratio: "3:4", tier: "2k", width: "1536", height: "2048" },
  { ratio: "3:4", tier: "4k", width: "2400", height: "3200" },
  { ratio: "21:9", tier: "1k", width: "1280", height: "544" },
  { ratio: "21:9", tier: "2k", width: "2560", height: "1088" },
  { ratio: "21:9", tier: "4k", width: "3840", height: "1600" },
];

export const IMAGE_SIZE_PRESET_BY_RATIO_TIER = IMAGE_SIZE_PRESETS.reduce<Record<string, Pick<ImageSizePreset, "width" | "height">>>(
  (items, preset) => {
    items[`${preset.ratio}:${preset.tier}`] = { width: preset.width, height: preset.height };
    return items;
  },
  {
    "auto:auto": { width: "2048", height: "2048" },
    "custom:custom": { width: "2048", height: "2048" },
  },
);
