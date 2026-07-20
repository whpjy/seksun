using System;
using NXOpen;

public class NxHello
{
    public static int Main(string[] args)
    {
        Session session = Session.GetSession();
        ListingWindow listingWindow = session.ListingWindow;

        listingWindow.Open();
        listingWindow.WriteLine("NX 自动测量工具：连接成功。");

        Part workPart = session.Parts.Work;
        if (workPart == null)
        {
            listingWindow.WriteLine("当前没有打开工作零件。");
        }
        else
        {
            listingWindow.WriteLine("当前零件：" + workPart.Leaf);
            listingWindow.WriteLine("第一步测试已通过。");
        }

        return 0;
    }

    public static int GetUnloadOption(string dummy)
    {
        return (int)Session.LibraryUnloadOption.Immediately;
    }
}
