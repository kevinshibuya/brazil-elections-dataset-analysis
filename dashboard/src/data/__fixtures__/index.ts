/**
 * Cross-language round-trip fixtures: a real, Python-encoded RR shard plus the
 * totals the exporter computed for it.
 *
 * The source of truth is `data/fixtures/golden_shard/` at the repo root and is
 * READ-ONLY. If the decoder disagrees with it, the bug is on one side of the
 * frozen `v = 1` schema — fix the code, never the fixture.
 *
 * Test-only: nothing under `src/data/__fixtures__/` is imported by app code,
 * so the base64 payload never reaches the production bundle.
 */

import expected from "./golden-expected.json";
import type { Bytes } from "../types";
import { GOLDEN_SHARD_BASE64, GOLDEN_SHARD_BYTES } from "./golden-shard.base64";

function fromBase64(base64: string): Uint8Array {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

/** Raw gzip bytes of `03158.json.gz` — SÃO LUIZ/RR, 22 sections, zone 4. */
export const goldenBytes: Uint8Array = fromBase64(GOLDEN_SHARD_BASE64);

if (goldenBytes.length !== GOLDEN_SHARD_BYTES)
  throw new Error(
    `golden fixture is ${goldenBytes.length} bytes, expected ${GOLDEN_SHARD_BYTES}`,
  );

/** `expected.json` from the same fixture directory (camelCase by convention). */
export const goldenExpected = expected;

/** The golden shard's municipality code. */
export const GOLDEN_CODE = expected.code;

/**
 * Re-gzip the given shard with its top-level `v` rewritten — the only way to
 * exercise the version gate without committing a second, deliberately-wrong
 * fixture.
 */
export async function withVersion(bytes: Bytes, version: number): Promise<Uint8Array> {
  const view = new Uint8Array(bytes.byteLength);
  view.set(
    ArrayBuffer.isView(bytes)
      ? new Uint8Array(bytes.buffer, bytes.byteOffset, bytes.byteLength)
      : new Uint8Array(bytes),
  );

  const inflated = new Response(
    new ReadableStream<BufferSource>({
      start(controller) {
        controller.enqueue(view);
        controller.close();
      },
    }).pipeThrough(new DecompressionStream("gzip")),
  );
  const document = JSON.parse(await inflated.text()) as Record<string, unknown>;
  document["v"] = version;

  const encoded = new TextEncoder().encode(JSON.stringify(document));
  const deflated = new Response(
    new ReadableStream<BufferSource>({
      start(controller) {
        controller.enqueue(encoded);
        controller.close();
      },
    }).pipeThrough(new CompressionStream("gzip")),
  );
  return new Uint8Array(await deflated.arrayBuffer());
}
