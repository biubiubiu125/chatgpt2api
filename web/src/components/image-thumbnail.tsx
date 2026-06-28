"use client";

import { useEffect, useMemo, useState } from "react";

import { request } from "@/lib/request";
import { cn } from "@/lib/utils";

type ImageThumbnailProps = {
  src: string;
  thumbnailSrc?: string;
  alt?: string;
  className?: string;
  imageClassName?: string;
};

export function getImageThumbnailUrl(src: string) {
  const marker = "/api/images/view/";
  const adminIndex = src.indexOf(marker);
  if (adminIndex >= 0) return `${src.slice(0, adminIndex)}/api/images/thumbnail/${src.slice(adminIndex + marker.length)}`;
  const legacyMarker = "/images/";
  const index = src.indexOf(legacyMarker);
  if (index >= 0) return `${src.slice(0, index)}/image-thumbnails/${src.slice(index + legacyMarker.length)}`;
  return src;
}

function isAuthenticatedImageSrc(src: string) {
  if (!src) return false;
  try {
    const base = typeof window !== "undefined" ? window.location.origin : "http://localhost";
    const url = new URL(src, base);
    return url.pathname.startsWith("/api/images/view/") || url.pathname.startsWith("/api/images/thumbnail/");
  } catch {
    return src.startsWith("/api/images/view/") || src.startsWith("/api/images/thumbnail/");
  }
}

function requestPath(src: string) {
  try {
    const url = new URL(src, window.location.origin);
    return `${url.pathname}${url.search}`;
  } catch {
    return src;
  }
}

async function fetchAuthenticatedImageSrc(src: string) {
  const response = await request.get(requestPath(src), { responseType: "blob" });
  return URL.createObjectURL(response.data as Blob);
}

export function ImageThumbnail({ src, thumbnailSrc, alt = "", className, imageClassName }: ImageThumbnailProps) {
  const initialSrc = useMemo(() => thumbnailSrc || getImageThumbnailUrl(src), [src, thumbnailSrc]);
  const initialDisplaySrc = useMemo(() => (isAuthenticatedImageSrc(initialSrc) ? "" : initialSrc), [initialSrc]);
  const [targetSrc, setTargetSrc] = useState(initialSrc);
  const [currentSrc, setCurrentSrc] = useState(initialDisplaySrc);

  useEffect(() => {
    setTargetSrc(initialSrc);
    setCurrentSrc(isAuthenticatedImageSrc(initialSrc) ? "" : initialSrc);
  }, [initialSrc]);

  useEffect(() => {
    let objectUrl = "";
    let cancelled = false;
    if (!isAuthenticatedImageSrc(targetSrc)) {
      setCurrentSrc(targetSrc);
      return;
    }
    void fetchAuthenticatedImageSrc(targetSrc)
      .then((url) => {
        objectUrl = url;
        if (!cancelled) setCurrentSrc(url);
      })
      .catch(() => {
        if (!cancelled && targetSrc !== src) setTargetSrc(src);
      });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [targetSrc, src]);

  return (
    <span className={cn("block overflow-hidden bg-stone-100", className)}>
      {currentSrc ? (
        <img
          src={currentSrc}
          alt={alt}
          className={cn("h-full w-full object-cover", imageClassName)}
          loading="lazy"
          decoding="async"
          onError={() => {
            if (targetSrc !== src) {
              setTargetSrc(src);
            }
          }}
        />
      ) : null}
    </span>
  );
}
