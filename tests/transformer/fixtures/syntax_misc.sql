declare
  -- anahtar kelimeye benzeyen tanımlayıcılar, tırnaklı adlar, kayıt tipleri
  type t_pair is record (k varchar2(10), v number default 0);
  "Mixed Case" number := .5;
  limit        pls_integer := 1e3;
  l_pair       t_pair;
  l_dt         date := date '2024-12-31';
  cursor c_acc (p_min in number default 0) is
    select a.account_id, a.type
      from accounts a
     where a.balance > p_min
       and a.name like 'A%'
       and exists (select 1 from blocked b where b.account_id = a.account_id);
  type t_ids is table of accounts.account_id%type index by pls_integer;
  l_ids        t_ids;
begin
  <<outer>>
  for r in c_acc(p_min => 100) loop
    continue when r.type = 'X';
    l_ids(l_ids.count + 1) := r.account_id;
    exit outer when l_ids.count >= limit;
  end loop outer;

  for r in (select x from (select level x from dual connect by level <= 3)) loop
    null;
  end loop;

  for i in reverse 1 .. 10 loop
    if mod(i, 2) = 0 and not (i between 4 and 6) then
      l_pair.v := l_pair.v + i;
    end if;
  end loop;

  while l_pair.v > 0 loop
    l_pair.v := l_pair.v - 1;
  end loop;

  case
    when l_pair.k is null then l_pair.k := 'none';
    when l_pair.k in ('a', 'b') then null;
    else l_pair.k := upper(l_pair.k);
  end case;

  l_pair.v := case l_pair.k when 'none' then -1 else 1 end;

  begin
    execute immediate 'truncate table tmp_x';
  exception
    when others then null;
  end;

  delete from tmp_y where id not in (1, 2, 3);
  dbms_output.put_line(sql%rowcount || ' satır silindi; "' || "Mixed Case" || '"');
  l_ids.delete;
  forall i in indices of l_ids
    update accounts set flagged = 'Y' where account_id = l_ids(i);
  rollback;
END;
/
