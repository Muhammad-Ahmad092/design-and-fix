import { db } from "./db";

export const logger = {
  info(msg: string) {
    console.log(msg);
  },
};

export function formatRow(sql: string, value: unknown) {
  return { sql, value, connected: typeof db !== "undefined" };
}
