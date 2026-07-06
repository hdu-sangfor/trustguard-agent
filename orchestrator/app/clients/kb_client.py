from __future__ import annotations

"""
KB Client 接入层：
- 对 orchestrator 暴露统一的知识库读取（retrieve）与经验沉淀写入（propose_experience）接口
- 内部实现可替换（MVP 默认 NullClient，后续在 `kb-retrieval-impl` 任务中补齐真实 Qdrant/LangChain 实现）

注意：本文件只定义契约与数据结构；向量检索/写入细节在后续任务中落地。
"""

from dataclasses import dataclass
from typing import Any
import os
import asyncio
import hashlib
from datetime import datetime


@dataclass(frozen=True)
class Hit:
    """Qdrant 命中统一结构，用于注入决策上下文。"""

    id: str
    collection: str
    score: float | None = None
    snippet: str = ""
    payload: dict[str, Any] | None = None


@dataclass(frozen=True)
class ExperienceCandidate:
    """经验候选（写入前的结构化中间态）。"""

    task_id: str
    phase: str
    skill_id: str
    todo_id: str | None
    agent_role: str | None
    target: str

    artifact_ref: str
    event_id: str | None
    memory_path: str | None

    # 向量检索可用的文本（summary 建议是尽量短且信息密度高）
    summary_text: str
    # 可选：上下文结构快照（payload 注入/过滤时用）
    context_snapshot: dict[str, Any] | None

    # gating 后写入时使用（pending/proven 等）
    effectiveness: str

    # KB-R1a：多租户/项目隔离（与检索 filter 对齐；可来自 target_context 或默认 env）
    workspace_id: str | None = None
    project_id: str | None = None

    # KB-R1b：产物 Chunk 指针 + 向量嵌入用短意图句（与 summary_text 分离）
    chunk_id: str | None = None
    intent_embed_text: str | None = None

    # provenance / 调试字段
    source: str = "runtime_experience"


class KBClient:
    """KB 访问抽象。"""

    async def retrieve(
        self,
        *,
        collection: str,
        query_text: str,
        top_k: int,
        filters: dict[str, Any] | None = None,
        quota_scope: str | None = None,
    ) -> list[Hit]:
        raise NotImplementedError

    async def retrieve_knowledge_tiered(
        self,
        *,
        query_text: str,
        top_k: int,
        filters: dict[str, Any] | None = None,
        quota_scope: str | None = None,
    ) -> list[Hit]:
        """kb-r2a：静态知识多分档检索与 boost；默认空（Null / 未实现）。"""
        return []

    async def propose_experience(self, candidate: ExperienceCandidate) -> str:
        raise NotImplementedError


def merge_experience_hits(primary: list[Hit], secondary: list[Hit], *, top_k: int) -> list[Hit]:
    """按 score 合并两路 experience 命中，同一 artifact_ref（否则 id）保留高分。"""
    merged: dict[str, Hit] = {}

    def _key(h: Hit) -> str:
        p = h.payload or {}
        if isinstance(p, dict):
            ar = str(p.get("artifact_ref") or "").strip()
            if ar:
                return f"ar:{ar}"
        return f"id:{h.id}"

    for h in primary + secondary:
        k = _key(h)
        prev = merged.get(k)
        if prev is None or (h.score or 0.0) > (prev.score or 0.0):
            merged[k] = h
    ranked = sorted(merged.values(), key=lambda x: -(x.score or 0.0))
    return ranked[: max(1, top_k)]


class NullKBClient(KBClient):
    """KB 关闭/依赖缺失时使用，保证主流程不阻断。"""

    async def retrieve(
        self,
        *,
        collection: str,
        query_text: str,
        top_k: int,
        filters: dict[str, Any] | None = None,
        quota_scope: str | None = None,
    ) -> list[Hit]:
        return []

    async def propose_experience(self, candidate: ExperienceCandidate) -> str:
        return ""


def _env_bool(name: str, default: bool = False) -> bool:
    v = (os.getenv(name) or "").strip().lower()
    if not v:
        return default
    return v in ("1", "true", "yes", "y", "on")


def _env_float(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def knowledge_hit_visible_for_retrieval(payload: dict[str, Any] | None) -> bool:
    """
    kb-r2b：Manual 录入可处于 pending，检索侧排除待审核条目（其它 kind 无该字段则放行）。
    """
    if not isinstance(payload, dict):
        return True
    rs = str(payload.get("review_status") or "").strip().lower()
    return rs != "pending"


def tiered_knowledge_collection(cfg: KBConfig, tier: str) -> str:
    """
    静态知识分档集合：unified 时一律写入主 knowledge collection（payload 带 kb_tier）；
    split 时写入 manual / cve / blogs 对应集合。
    """
    t = (tier or "").strip().lower()
    if not cfg.static_tier_split:
        return cfg.knowledge_collection
    if t == "manual":
        return cfg.knowledge_manual_collection
    if t == "cve":
        return cfg.knowledge_cve_collection
    if t == "blogs":
        return cfg.knowledge_blogs_collection
    return cfg.knowledge_collection


def manual_knowledge_target_collection(cfg: KBConfig) -> str:
    """Manual 静态条目的目标集合：split → manual collection，否则 unified knowledge。"""
    return tiered_knowledge_collection(cfg, "manual")


def _env_int_clamped(name: str, default: int, *, lo: int, hi: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return max(lo, min(int(raw), hi))
    except ValueError:
        return default


@dataclass(frozen=True)
class KBConfig:
    enabled: bool
    qdrant_url: str
    knowledge_collection: str
    experience_collection: str
    # kb-r1d：可选第二集合；legacy 点常无 workspace，用宽松 filter 单独检索再合并
    experience_legacy_collection: str | None
    # kb-r2a：true 时静态知识从 manual/cve/blogs 三个 collection 检索再合并
    static_tier_split: bool
    knowledge_manual_collection: str
    knowledge_cve_collection: str
    knowledge_blogs_collection: str
    boost_manual: float
    boost_cve: float
    boost_blogs: float
    # unified 模式：先拉取若干候选再按 kb_tier boost 重排；<=0 则用 max(20, top_k*5)
    knowledge_unified_prefetch: int
    top_k: int
    embedding_model: str
    embed_base_url: str
    embed_api_key: str
    auto_create: bool


def get_kb_config() -> KBConfig:
    enabled = _env_bool("KB_ENABLED", default=False)
    qdrant_url = (os.getenv("KB_QDRANT_URL") or "http://qdrant:6333").strip()
    knowledge_collection = (os.getenv("KB_COLLECTION_KNOWLEDGE") or "trustguard_knowledge").strip()
    experience_collection = (os.getenv("KB_COLLECTION_EXPERIENCE") or "trustguard_experience").strip()
    _leg = (os.getenv("KB_EXPERIENCE_LEGACY_COLLECTION") or "").strip()
    experience_legacy_collection = _leg or None
    static_tier_split = _env_bool("KB_STATIC_TIER_SPLIT", default=False)
    knowledge_manual_collection = (os.getenv("KB_COLLECTION_KNOWLEDGE_MANUAL") or "trustguard_kb_manual").strip()
    knowledge_cve_collection = (os.getenv("KB_COLLECTION_KNOWLEDGE_CVE") or "trustguard_kb_cve").strip()
    knowledge_blogs_collection = (os.getenv("KB_COLLECTION_KNOWLEDGE_BLOGS") or "trustguard_kb_blogs").strip()
    boost_manual = _env_float("KB_BOOST_MANUAL", 1.5)
    boost_cve = _env_float("KB_BOOST_CVE", 1.2)
    boost_blogs = _env_float("KB_BOOST_BLOGS", 0.8)
    knowledge_unified_prefetch = _env_int_clamped("KB_UNIFIED_KNOWLEDGE_PREFETCH", 0, lo=0, hi=500)
    top_k = max(1, int(os.getenv("KB_TOP_K") or "5"))
    # 向量模型优先使用 KB_EMBED_MODEL，兼容旧的 KB_EMBEDDING_MODEL
    embedding_model = (
        os.getenv("KB_EMBED_MODEL")
        or os.getenv("KB_EMBEDDING_MODEL")
        or "text-embedding-3-small"
    ).strip()
    # 向量引擎优先使用 KB_EMBED_*，未配置时退回 OPENAI_*（兼容原有行为）
    embed_base_url = (os.getenv("KB_EMBED_BASE_URL") or os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1").strip()
    embed_api_key = (os.getenv("KB_EMBED_API_KEY") or os.getenv("OPENAI_API_KEY") or "").strip()
    auto_create = _env_bool("KB_AUTO_CREATE", default=False)

    return KBConfig(
        enabled=enabled,
        qdrant_url=qdrant_url,
        knowledge_collection=knowledge_collection,
        experience_collection=experience_collection,
        experience_legacy_collection=experience_legacy_collection,
        static_tier_split=static_tier_split,
        knowledge_manual_collection=knowledge_manual_collection,
        knowledge_cve_collection=knowledge_cve_collection,
        knowledge_blogs_collection=knowledge_blogs_collection,
        boost_manual=boost_manual,
        boost_cve=boost_cve,
        boost_blogs=boost_blogs,
        knowledge_unified_prefetch=knowledge_unified_prefetch,
        top_k=top_k,
        embedding_model=embedding_model,
        embed_base_url=embed_base_url,
        embed_api_key=embed_api_key,
        auto_create=auto_create,
    )


class QdrantKBClient(KBClient):
    """真实 Qdrant/LangChain 实现的占位类（后续 kb-retrieval-impl 补齐）。"""

    def __init__(self, cfg: KBConfig) -> None:
        self._cfg = cfg
        # 向量服务使用单独的 KB_EMBED_*，未配置时退回 OPENAI_*（在 get_kb_config 中已处理）
        self._embed_api_key = cfg.embed_api_key
        self._embed_base_url = cfg.embed_base_url
        self._collections_checked = False
        self._collections_check_lock = asyncio.Lock()

    def _stable_point_id(self, seed: str) -> str:
        digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
        return digest[:24]

    async def _ensure_collections(self) -> None:
        """
        KB_ENABLED 时对 knowledge/experience collection 做存在性校验：
        - 若 KB_AUTO_CREATE=true 且缺失，则创建（基于 embedding 维度推导向量 size）
        """
        if self._collections_checked:
            return
        async with self._collections_check_lock:
            if self._collections_checked:
                return

            try:
                from qdrant_client import QdrantClient
                from qdrant_client.http.models import VectorParams, Distance
            except Exception:
                self._collections_checked = True
                return

            client = QdrantClient(url=self._cfg.qdrant_url)
            try:
                vec = await self._embed_query("KB dimension probe", skip_quota=True)
                vector_size = len(vec)
            except Exception:
                vector_size = None

            cols: list[str] = [self._cfg.knowledge_collection, self._cfg.experience_collection]
            if self._cfg.static_tier_split:
                cols.extend(
                    [
                        self._cfg.knowledge_manual_collection,
                        self._cfg.knowledge_cve_collection,
                        self._cfg.knowledge_blogs_collection,
                    ]
                )
            seen: set[str] = set()
            for col in cols:
                if col in seen:
                    continue
                seen.add(col)
                try:
                    client.get_collection(collection_name=col)
                except Exception:
                    if not self._cfg.auto_create or not vector_size:
                        continue
                    try:
                        client.create_collection(
                            collection_name=col,
                            vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
                        )
                    except Exception:
                        continue
            self._collections_checked = True

    @staticmethod
    def _iso_now() -> str:
        return datetime.utcnow().isoformat() + "Z"

    @staticmethod
    def _filters_to_qdrant(filters: dict[str, Any] | None) -> Any:
        """把简单 dict filters 转成 Qdrant Filter（仅支持标量匹配）。"""
        if not filters:
            return None
        try:
            from qdrant_client.http.models import Filter, FieldCondition, MatchValue
        except Exception:
            return None

        conditions: list[Any] = []
        for k, v in (filters or {}).items():
            if v is None:
                continue
            if isinstance(v, (str, int, float, bool)):
                conditions.append(FieldCondition(key=str(k), match=MatchValue(value=v)))
        if not conditions:
            return None
        return Filter(must=conditions)

    async def _embed_query(
        self,
        text: str,
        *,
        quota_scope: str | None = None,
        skip_quota: bool = False,
    ) -> list[float]:
        if not skip_quota:
            from app.kb_embed_quota import acquire_kb_embed_slot

            await acquire_kb_embed_slot(quota_scope)

        try:
            from langchain_openai import OpenAIEmbeddings
        except Exception as exc:
            raise RuntimeError("langchain_openai is required for KB_ENABLED") from exc

        # embed_query 在内部为同步阻塞；用 to_thread 避免阻塞事件循环
        embeddings = OpenAIEmbeddings(
            model=self._cfg.embedding_model,
            api_key=self._embed_api_key,
            openai_api_base=self._embed_base_url,
        )
        vec: list[float] = await asyncio.to_thread(embeddings.embed_query, text)
        return vec

    def _payload_snippet(self, payload: dict[str, Any] | None) -> str:
        from app.kb_experience_payload import experience_payload_snippet_for_display

        if not isinstance(payload, dict):
            return ""
        return experience_payload_snippet_for_display(payload)

    async def retrieve(
        self,
        *,
        collection: str,
        query_text: str,
        top_k: int,
        filters: dict[str, Any] | None = None,
        quota_scope: str | None = None,
    ) -> list[Hit]:
        try:
            from qdrant_client import QdrantClient
            from qdrant_client.http.models import VectorParams, Distance, PointStruct
        except Exception as exc:
            raise RuntimeError("qdrant-client is required for KB_ENABLED") from exc

        if not self._embed_api_key:
            raise RuntimeError("KB_EMBED_API_KEY (or OPENAI_API_KEY) is required for KB_ENABLED (embeddings).")

        client = QdrantClient(url=self._cfg.qdrant_url)

        # 启动时校验/可选创建（只做一次，避免每次 tick 重复）
        await self._ensure_collections()

        try:
            vector = await self._embed_query(query_text, quota_scope=quota_scope)
        except Exception as exc:
            from app.kb_embed_quota import EmbedQuotaExceeded

            if isinstance(exc, EmbedQuotaExceeded):
                return []
            raise
        qfilter = self._filters_to_qdrant(filters)

        def _search() -> Any:
            return client.search(
                collection_name=collection,
                query_vector=vector,
                query_filter=qfilter,
                limit=top_k,
                with_payload=True,
            )

        try:
            hits = await asyncio.to_thread(_search)
        except Exception:
            return []

        out: list[Hit] = []
        for h in hits or []:
            payload = getattr(h, "payload", None) or {}
            pid = getattr(h, "id", None)
            pl = payload if isinstance(payload, dict) else None
            if not knowledge_hit_visible_for_retrieval(pl):
                continue
            out.append(
                Hit(
                    id=str(pid) if pid is not None else "",
                    collection=collection,
                    score=float(getattr(h, "score", 0.0)) if getattr(h, "score", None) is not None else None,
                    snippet=self._payload_snippet(pl),
                    payload=pl,
                )
            )
        return out

    async def retrieve_knowledge_tiered(
        self,
        *,
        query_text: str,
        top_k: int,
        filters: dict[str, Any] | None = None,
        quota_scope: str | None = None,
    ) -> list[Hit]:
        from app.kb_static_tiers import boost_map_from_config, merge_tagged_hits_with_boost, normalize_kb_tier

        boosts = boost_map_from_config(
            boost_manual=self._cfg.boost_manual,
            boost_cve=self._cfg.boost_cve,
            boost_blogs=self._cfg.boost_blogs,
        )
        tk = max(1, int(top_k))

        if self._cfg.static_tier_split:
            tagged: list[tuple[Hit, str]] = []
            for tier, col in (
                ("manual", self._cfg.knowledge_manual_collection),
                ("cve", self._cfg.knowledge_cve_collection),
                ("blogs", self._cfg.knowledge_blogs_collection),
            ):
                hits = await self.retrieve(
                    collection=col,
                    query_text=query_text,
                    top_k=tk,
                    filters=filters,
                    quota_scope=quota_scope,
                )
                for h in hits:
                    tagged.append((h, tier))
            return merge_tagged_hits_with_boost(tagged, boosts=boosts, top_k=tk)

        prefetch = int(self._cfg.knowledge_unified_prefetch)
        if prefetch <= 0:
            prefetch = max(20, tk * 5)
        prefetch = min(max(prefetch, tk), 500)

        hits = await self.retrieve(
            collection=self._cfg.knowledge_collection,
            query_text=query_text,
            top_k=prefetch,
            filters=filters,
            quota_scope=quota_scope,
        )
        tagged2: list[tuple[Hit, str]] = []
        for h in hits:
            tier = normalize_kb_tier((h.payload or {}).get("kb_tier"))
            tagged2.append((h, tier))
        return merge_tagged_hits_with_boost(tagged2, boosts=boosts, top_k=tk)

    async def propose_experience(self, candidate: ExperienceCandidate) -> str:
        try:
            from qdrant_client import QdrantClient
            from qdrant_client.http.models import VectorParams, Distance, PointStruct
        except Exception as exc:
            raise RuntimeError("qdrant-client is required for KB_ENABLED") from exc

        if not self._embed_api_key:
            raise RuntimeError("KB_EMBED_API_KEY (or OPENAI_API_KEY) is required for KB_ENABLED (embeddings).")

        collection = self._cfg.experience_collection
        client = QdrantClient(url=self._cfg.qdrant_url)

        # 启动时校验/可选创建（只做一次）
        await self._ensure_collections()

        full_embed = (os.getenv("KB_EXPERIENCE_EMBED_FULL_SUMMARY") or "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        intent = (getattr(candidate, "intent_embed_text", None) or "").strip()
        if full_embed:
            embed_text = (candidate.summary_text or "").strip() or candidate.task_id
        else:
            embed_text = intent or ""
            if not embed_text and candidate.summary_text:
                embed_text = (candidate.summary_text or "").strip().split("\n", 1)[0].strip()
            if not embed_text:
                embed_text = candidate.task_id
        qscope = (candidate.workspace_id or candidate.project_id or "").strip() or None
        try:
            vector = await self._embed_query((embed_text or candidate.task_id)[:8000], quota_scope=qscope)
        except Exception as exc:
            from app.kb_embed_quota import EmbedQuotaExceeded

            if isinstance(exc, EmbedQuotaExceeded):
                return ""
            raise

        # 幂等：同 artifact_ref 的经验写入使用同一个 point id（upsert 覆盖）
        # 这样同一证据会被稳定映射到同一 doc/candidate id，便于“经验 -> 证据”回溯与重复写入的安全更新。
        seed = candidate.artifact_ref or f"{candidate.task_id}|{candidate.skill_id}|{candidate.phase}"
        point_id = self._stable_point_id(seed)

        now = self._iso_now()
        from app.kb_experience_payload import experience_payload_dict_for_upsert

        try:
            payload = experience_payload_dict_for_upsert(candidate, now_iso=now)
        except Exception:
            return ""

        def _upsert() -> Any:
            return client.upsert(
                collection_name=collection,
                points=[
                    PointStruct(
                        id=point_id,
                        vector=vector,
                        payload=payload,
                    )
                ],
            )

        try:
            await asyncio.to_thread(_upsert)
        except Exception:
            return ""
        return point_id

    async def upsert_manual_knowledge_document(
        self,
        *,
        title: str,
        body: str,
        embed_text: str | None,
        phase: str,
        project_id: str | None,
        workspace_id: str | None,
        tags: list[str],
        source_url: str | None,
        reviewed_by: str | None,
        last_reviewed_at: str | None,
        tool_id: str,
        doc_key: str | None,
        review_status: str,
        context_snapshot: dict[str, Any] | None,
    ) -> str:
        """kb-r2b：管理员录入 Manual 静态知识（Qdrant upsert）。"""
        try:
            from qdrant_client import QdrantClient
            from qdrant_client.http.models import PointStruct
        except Exception as exc:
            raise RuntimeError("qdrant-client is required for KB_ENABLED") from exc

        if not self._embed_api_key:
            raise RuntimeError("KB_EMBED_API_KEY (or OPENAI_API_KEY) is required for KB_ENABLED (embeddings).")

        await self._ensure_collections()

        b = (body or "").strip()
        if not b:
            return ""

        et = (embed_text or "").strip() or b
        qscope = (workspace_id or project_id or "").strip() or None
        try:
            vector = await self._embed_query(et[:8000], quota_scope=qscope)
        except Exception as exc:
            from app.kb_embed_quota import EmbedQuotaExceeded

            if isinstance(exc, EmbedQuotaExceeded):
                return ""
            raise

        key_src = (doc_key or "").strip() or "|".join(
            [
                (project_id or "").strip(),
                (workspace_id or "").strip(),
                (title or "").strip()[:200],
                b[:400],
            ]
        )
        point_id = self._stable_point_id(f"manual|{key_src}")
        collection = manual_knowledge_target_collection(self._cfg)
        now = self._iso_now()
        reviewed_at = (last_reviewed_at or "").strip() or now
        rs = (review_status or "approved").strip().lower()
        if rs not in ("pending", "approved"):
            rs = "approved"

        payload: dict[str, Any] = {
            "kind": "knowledge",
            "schema_version": "kb-know-v1",
            "kb_tier": "manual",
            "phase": (phase or "RECON").strip(),
            "tool_id": (tool_id or "manual_admin").strip() or "manual_admin",
            "tags": list(tags or []),
            "source": "manual",
            "summary": b,
            "title": (title or "").strip(),
            "source_url": (source_url or "").strip(),
            "review_status": rs,
            "reviewed_by": (reviewed_by or "").strip(),
            "last_reviewed_at": reviewed_at,
            "created_at": now,
            "updated_at": now,
            "project_id": (project_id or "").strip(),
            "workspace_id": (workspace_id or "").strip(),
        }
        if isinstance(context_snapshot, dict) and context_snapshot:
            payload["context_snapshot"] = context_snapshot

        client = QdrantClient(url=self._cfg.qdrant_url)

        def _upsert() -> Any:
            return client.upsert(
                collection_name=collection,
                points=[PointStruct(id=point_id, vector=vector, payload=payload)],
            )

        await asyncio.to_thread(_upsert)
        return point_id

    async def upsert_pipeline_knowledge_vector(
        self,
        *,
        collection: str,
        point_id: str,
        embed_text: str,
        payload: dict[str, Any],
    ) -> str:
        """kb-r2c：管道写入静态知识（CVE/Blogs 等），由调用方构造 payload 与稳定 point_id。"""
        try:
            from qdrant_client import QdrantClient
            from qdrant_client.http.models import PointStruct
        except Exception as exc:
            raise RuntimeError("qdrant-client is required for KB_ENABLED") from exc

        if not self._embed_api_key:
            raise RuntimeError("KB_EMBED_API_KEY (or OPENAI_API_KEY) is required for KB_ENABLED (embeddings).")

        pid = (point_id or "").strip()
        if not pid:
            return ""

        await self._ensure_collections()
        et = (embed_text or "").strip()
        if not et:
            return ""

        try:
            vector = await self._embed_query(et[:8000], quota_scope="kb_pipeline")
        except Exception as exc:
            from app.kb_embed_quota import EmbedQuotaExceeded

            if isinstance(exc, EmbedQuotaExceeded):
                return ""
            raise
        client = QdrantClient(url=self._cfg.qdrant_url)
        col = (collection or "").strip()
        if not col:
            return ""

        def _upsert() -> Any:
            return client.upsert(
                collection_name=col,
                points=[PointStruct(id=pid, vector=vector, payload=payload)],
            )

        await asyncio.to_thread(_upsert)
        return pid

    async def approve_manual_knowledge_review(
        self,
        *,
        point_id: str,
        reviewed_by: str | None,
    ) -> tuple[bool, str]:
        """
        将 manual knowledge 点的 review_status 置为 approved。
        返回 (ok, collection)；ok=False 表示未找到或类型不符。
        """
        try:
            from qdrant_client import QdrantClient
        except Exception as exc:
            raise RuntimeError("qdrant-client is required for KB_ENABLED") from exc

        pid = (point_id or "").strip()
        if not pid:
            return False, ""

        await self._ensure_collections()
        collection = manual_knowledge_target_collection(self._cfg)
        client = QdrantClient(url=self._cfg.qdrant_url)

        def _load() -> dict[str, Any] | None:
            rows = client.retrieve(collection_name=collection, ids=[pid], with_payload=True)
            if not rows:
                return None
            pl = getattr(rows[0], "payload", None) or {}
            return pl if isinstance(pl, dict) else None

        pl = await asyncio.to_thread(_load)
        if not pl:
            return False, collection
        if str(pl.get("kind") or "") != "knowledge":
            return False, collection
        if str(pl.get("source") or "").strip().lower() != "manual":
            return False, collection

        now = self._iso_now()
        reviewer = (reviewed_by or "").strip()

        def _patch() -> None:
            client.set_payload(
                collection_name=collection,
                payload={
                    "review_status": "approved",
                    "reviewed_by": reviewer,
                    "last_reviewed_at": now,
                    "updated_at": now,
                },
                points=[pid],
            )

        await asyncio.to_thread(_patch)
        return True, collection


_KB_CLIENT: KBClient | None = None


def get_kb_client() -> KBClient:
    """
    返回可用 KB Client：
    - KB_ENABLED=false：返回 NullKBClient
    - KB_ENABLED=true：返回真实实现（后续在 kb-retrieval-impl 中补齐）
    """

    global _KB_CLIENT
    if _KB_CLIENT is not None:
        return _KB_CLIENT

    cfg = get_kb_config()
    if not cfg.enabled:
        _KB_CLIENT = NullKBClient()
        return _KB_CLIENT

    # 当前版本占位实现：后续 kb-retrieval-impl 补齐真实检索/写入逻辑
    _KB_CLIENT = QdrantKBClient(cfg)
    return _KB_CLIENT
