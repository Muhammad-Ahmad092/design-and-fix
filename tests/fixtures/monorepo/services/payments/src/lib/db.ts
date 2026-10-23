import { formatRow } from "./util";

export const db = {
  async query(sql: string, params: unknown[] = []) {
    return { rows: params.map((p) => formatRow(sql, p)) };
  },
};
