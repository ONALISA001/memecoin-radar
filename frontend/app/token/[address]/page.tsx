import TokenPage from "@/components/tokenpage";

export default async function Page({ params }: { params: Promise<{ address: string }> }) {
  const { address } = await params;
  return <TokenPage address={address} />;
}
