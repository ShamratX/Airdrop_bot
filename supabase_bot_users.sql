-- Run once in Supabase → SQL Editor (required for user progress; CSV is buyers-only)

create table if not exists bot_users (
  user_id bigint primary key,
  username text default '',
  first_name text default '',
  referrer_id bigint,
  referral_count int default 0,
  channel_joined boolean default false,
  group_joined boolean default false,
  x_done boolean default false,
  rt_done boolean default false,
  like_done boolean default false,
  referrals_done boolean default false,
  airdrop_complete boolean default false,
  wallet_address text default '',
  step5_skipped boolean default false,
  presale_tier text default '',
  txid text default '',
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);
