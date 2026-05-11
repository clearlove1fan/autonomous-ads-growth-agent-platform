import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection

from ads_growth_agent.contracts import AdvertiserBrief
from ads_growth_agent.persistence.partitioning import partition_bucket
from ads_growth_agent.persistence.schema import advertisers, tenants


def upsert_tenant_and_advertiser(
    connection: Connection,
    brief: AdvertiserBrief,
    *,
    tenant_id: str,
    upserted_by: str,
) -> None:
    tenant_metadata = {"upserted_by": upserted_by}
    tenant_stmt = (
        pg_insert(tenants)
        .values(
            tenant_id=tenant_id,
            display_name="Default Ads Growth Tenant",
            region="us",
            status="active",
            metadata=tenant_metadata,
        )
        .on_conflict_do_update(
            index_elements=[tenants.c.tenant_id],
            set_={
                "status": "active",
                "metadata": tenant_metadata,
                "updated_at": sa.func.now(),
            },
        )
    )
    connection.execute(tenant_stmt)

    advertiser_metadata = {"upserted_by": upserted_by}
    advertiser_stmt = (
        pg_insert(advertisers)
        .values(
            tenant_id=tenant_id,
            advertiser_id=brief.advertiser_id,
            name=brief.product_name,
            industry=brief.product_category,
            target_markets=[brief.target_market],
            status="active",
            metadata=advertiser_metadata,
            partition_key=brief.advertiser_id,
            partition_bucket=partition_bucket(brief.advertiser_id),
        )
        .on_conflict_do_update(
            index_elements=[advertisers.c.tenant_id, advertisers.c.advertiser_id],
            set_={
                "name": brief.product_name,
                "industry": brief.product_category,
                "target_markets": [brief.target_market],
                "status": "active",
                "metadata": advertiser_metadata,
                "partition_key": brief.advertiser_id,
                "partition_bucket": partition_bucket(brief.advertiser_id),
                "updated_at": sa.func.now(),
            },
        )
    )
    connection.execute(advertiser_stmt)
